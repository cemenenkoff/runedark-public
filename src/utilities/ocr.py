from operator import itemgetter
from pathlib import Path
from typing import Dict, List, Union

import cv2
import numpy as np

if __name__ == "__main__":
    import sys

    # Go up one level to facilitate importing from `utilities` below.
    sys.path[0] = str(Path(sys.path[0]).parents[0])

import utilities.debug as debug
from utilities.color_util import Color, ColorPalette, isolate_colors
from utilities.geometry import Rectangle
from utilities.mappings.problematic_chars import PROBLEMATIC_CHARS

PATH_FONT: Path = Path(__file__).parent.joinpath("fonts")
FontDict = Dict[str, cv2.Mat]


def load_font(font: str) -> FontDict:
    """Load a font's alphabet as a dictionary of BGR matrix images.

    A font dictionary used for OCR usually contains grayscale images, so it may be
    confusing to note that this function loads the images in BGR format. This is by
    design, however, as enforcing BGR image format improves OCR performance.

    Remember that by definition, a grayscale image is represented as a SINGLE-channel
    image, where each pixel value represents the intensity of the pixel, typically
    ranging from 0 (black) to 255 (white).

    Additionally, recall that a BGR image is a color image represented as a
    THREE-channel image, where each pixel has three color channels: Blue (B), Green
    (G), and Red (R). Each channel typically ranges from 0 to 255, representing the
    intensity of each color component.

    Even though a BGR-formatted image is not the same format as a single-channel,
    grayscale image, grayscale information can still be represented within a BGR image
    format. When an image is grayscale in BGR format, all three color channels have the
    same value, and when taken together, represent the intensity of the grayscale pixel.

    Args:
        font (str): The name of the font to load.

    Returns:
        FontDict: A dictionary of {"char": BGR image matrix} key-value pairs.
    """
    font_folder = PATH_FONT.joinpath(font)
    pathlist = font_folder.rglob("*.bmp")
    alphabet = {}
    for path in pathlist:
        name = int(path.stem)
        key = chr(name)
        value = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        alphabet[key] = value
    return alphabet


# Each listed font corresponds to a folder filled with images of varying dimensions, as
# each image's size is determined by the relative size of the character it represents.
#     BOLD_12 - Main text, top-left mouseover text, and overhead chat.
#     PLAIN_11 - RuneLite plug-ins and small interface text (e.g. orbs)
#     PLAIN_12 - Chatbox text and medium interface text.
#     QUILL - Large bold quest text.
#     QUILL_8 - Small quest text.
# Each character image is M pixels wide by N pixels tall.
PLAIN_11 = load_font("plain_11")  # Dimensions are (3-to-11) pixels x 12 pixels.
PLAIN_12 = load_font("plain_12")  # Dimensions are (3-to-13) pixels x 16 pixels.
BOLD_12 = load_font("bold_12")  # Dimensions are (4-to-14) pixels x 16 pixels.
QUILL = load_font("quill")  # Dimensions are (4-to-24) pixels x 31 pixels.
QUILL_8 = load_font("quill_8")  # Dimensions are (3-to-16) pixels x 20 pixels.


def scrape_text(
    rect: Rectangle,
    font: FontDict,
    colors: Union[Color, List[Color]],
    exclude_chars: Union[str, List[str]] = PROBLEMATIC_CHARS,
    include_only_chars: Union[str, List[str]] = None,
) -> str:
    """Extract text from a `Rectangle`.

    Args:
        rect (Rectangle): The `Rectangle` to search within.
        font (FontDict): A dictionary of {"char": image matrix} key-value pairs
            representing the font of the text to search for.
        color (Union[Color, List[Color]]): The OpenCV-style BGR color(s) of the
            text to search for. If multiple colors are provided, then a search for text
            with any the specified colors is performed.
        exclude_chars (Union[str, List[str]], optional): Characters to exclude when
            searching for text matches. Defaults to `PROBLEMATIC_CHARS`.
        include_only_chars (Union[str, List[str]], optional): Characters to include
            exclusively when searching for text matches. Defaults to None.

    Returns:
        str: A single string containing all found text, in order, with no newlines nor
            spaces.
    """
    # Screenshot and isolate colors.
    img_bgr = rect.screenshot()
    image = isolate_colors(img_bgr, colors)
    result = ""
    char_list = []
    for char in font:
        if include_only_chars is not None:
            if char not in include_only_chars:
                continue
        elif char == " " or char in exclude_chars:
            continue
        # Template match the character in the image. Note that we trim off the first 1
        # or 2 rows of pixels from the template (depending on the font) to aid in
        # `matchTemplate` finding a match.
        row_skip = 2 if font is PLAIN_12 else 1
        correlation = cv2.matchTemplate(
            image, font[char][row_skip:], cv2.TM_CCOEFF_NORMED
        )
        # Note that `correlation` is a grayscale image with values between 0 and 1,
        # indicating the match strength of the image to the template at each pixel
        # location. Each element of `correlation` represents the correlation
        # coefficient computed for the position of the template, as measured by the
        # top-left corner of the template within the main image. The dimensions of this
        # output image are (W - w + 1) x (H - h + 1).
        y_mins, x_mins = np.where(correlation >= 0.98)
        # For example, imagine a 3x4 pixel grid with a 2x2 template. The resulting
        # `correlation` array would have (4 - 2 + 1, 3 - 2 + 1) = (3, 2) dimensions. If
        # we think about sliding the 2x2 template around the 3x4 pixel grid, it only
        # has 6 valid positions, each corresponding to a distinct coordinate for the
        # template's upper-left corner. Recall that `Rectangle` objects have coordinate
        # systems with an origin in the upper left hand corner, hence the top-left
        # corner will have the rectangle's minimum x-value, and also it's minimum
        # y-value.

        # Add each matched instance of the current character to a list along with its
        # top-left coordinate (e.g. char_list = [['A', 10, 5], ['A', 30, 25]]).
        char_list.extend([char, x, y] for x, y in zip(x_mins, y_mins))
    # Sort the char list based on which ones appear closest to the image top-left.
    char_list = sorted(char_list, key=itemgetter(2, 1))  # Sort by y first, then by x.
    # Lastly, join the characters into one continuous string.
    return result.join(letter for letter, _, _ in char_list)


def find_textbox(
    text: Union[str, List[str]],
    rect: Rectangle,
    font: FontDict,
    colors: Union[Color, List[Color]],
) -> List[Rectangle]:
    """Return exact text matches in a `Rectangle` as bounded `Rectangle` objects.

    Note that `text` is case-sensitive.

    Args:
        text (Union[str, List[str]]): The text to search for. It can be a phrase, a
            single word, or a list of strings to search for individually. Note that if
            a list of strings is provided, they are not separately distinguished in the
            output list.
        rect (Rectangle): The `Rectangle` to search within.
        font (FontDict): The font type to search for.
        colors (Union[Color, List[Color]]): The BGR colors of the text to search
            for. If multiple colors are provided, then a search for text with any of
            the specified colors is performed.
    Returns:
        List[Rectangle]: A list of `Rectangle` objects, each corresponding to a
            bounding box of found text within the given `rect`.
    """
    img_bgr = rect.screenshot()  # Screenshot and isolate colors.
    image = isolate_colors(img_bgr, colors)  # White characters on a black background.
    chars = "".join(set("".join(text))).replace(" ", "")  # Distinct input characters.
    char_list = []
    row_skip = 2 if font is PLAIN_12 else 1
    # This small row skip is is crucial for accurately scraping 'PLAIN_12' text. It
    # aligns characters and removes excess padding, improving matching consistency with
    # the target image.
    for char in chars:
        try:
            template = font[char][row_skip:]
        except KeyError:
            text = text.replace(char, "")
            print(f"Font does not contain character: {char}. Omitting from search.")
            continue
        correlation = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        y_mins, x_mins = np.where(correlation >= 0.98)
        char_list.extend([char, x, y] for x, y in zip(x_mins, y_mins))

    # Sort the chars based on which ones appear closest to the top-left of the image.
    char_list = sorted(char_list, key=itemgetter(2, 1))  # Sort by y first, then by x.
    haystack = "".join(char[0] for char in char_list)
    if isinstance(text, str):
        text = [text]
    words_found: List[Rectangle] = []
    for word in text:
        word = word.replace(" ", "")
        for i, _ in enumerate(haystack):
            # Run a `word`-length character box left-to-right along a the haystack.
            if haystack[i : i + len(word)] == word:
                # Get the position of the first letter.
                left, top = char_list[i][1], char_list[i][2]  # Corresponds to x, y.
                h, w = font[word[-1]].shape[:2]  # Pixel dimensions of the last letter.
                # Get width (and height too, because they are the same for all letters).
                width = char_list[i + len(word) - 1][1] - left + w
                words_found.append(
                    Rectangle(left + rect.left, top + rect.top, width, h)
                )
                i += len(word)
    return words_found


if __name__ == "__main__":
    """Run this file directly to test OCR.

    An active instance of RuneLite must be open for these tests to work correctly."""
    CP = ColorPalette()
    win = debug.get_test_window()  # Make RuneLite the active window.
    # Remember that the mouseover area is subtracted from the game view!
    test_styles = [
        # "prayer_orb_text",
        # "game_view",
        "chatbox",
        # "mouseover",
    ]
    area = None
    for test_style in test_styles:
        if test_style == "game_view":
            area = win.game_view
            font = BOLD_12
            colors = [CP.bgr.WHITE_DROPDOWN_TEXT, CP.bgr.CYAN_DROPDOWN_TEXT]
            text = ["Walk", "here", "Cancel"]
            found_rects = find_textbox(text, area, font, colors)
        elif test_style == "chatbox":
            area = win.chat
            font = PLAIN_12
            colors = [CP.bgr.BLACK, CP.bgr.BLUE]
            text = ["Welcome", "Old", "RuneScape", "*"]
            found_rects = find_textbox(text, area, font, colors)
        elif test_style == "mouseover":
            area = win.mouseover
            font = BOLD_12
            colors = [CP.bgr.OFF_WHITE_TEXT, CP.bgr.OFF_CYAN_TEXT]
            text = [
                "Bank",
                "Bank booth",
                "Smelt",
                "Furnace",
                "Chop",
                "tree",
                "Tree",
                "Yew",
            ]
            found_rects = find_textbox(text, area, font, colors)
        elif test_style == "prayer_orb_text":
            area = win.prayer_orb_text
            font = PLAIN_11
            colors = [
                CP.bgr.ORB_TEXT_100_90,
                CP.bgr.ORB_TEXT_90_80,
                CP.bgr.ORB_TEXT_80_70,
                CP.bgr.ORB_TEXT_70_60,
                CP.bgr.ORB_TEXT_60_50,
                CP.bgr.ORB_TEXT_50_40,
                CP.bgr.ORB_TEXT_40_30,
                CP.bgr.ORB_TEXT_30_20,
                CP.bgr.ORB_TEXT_20_10,
                CP.bgr.ORB_TEXT_10_0,
            ]
            image = area.screenshot()
            debug.save_image("ocr-initial-screenshot.png", image)
            text = scrape_text(
                area, font, colors, include_only_chars=[str(i) for i in range(10)]
            )
            found_rects = find_textbox(text, area, font, colors)

        image = area.screenshot()  # Screenshot the starting area and save it.
        debug.save_image("ocr-initial-screenshot.png", image)
        print(test_style.replace("_", " ").title())
        found_text = scrape_text(area, font, colors)
        print("OCR Area-wide Scraped Text: ", found_text)
        found_rects = find_textbox(text, area, font, colors)
        print(f"Strings Searched: {text}")
        print(f"{len(text)} words searched, {len(found_rects)} rectangles found.")
        image = np.array(image)
        for rect in found_rects:
            # Get coordinates for each `Rectangle` relative to the `area`.
            x, y, w, h = (
                rect.left - area.left,
                rect.top - area.top,
                rect.width,
                rect.height,
            )
            # Draw the rectangle in bright green onto its containing image.
            cv2.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 1)
        debug.save_image("ocr-result.png", image)  # Save the modified image.
        cv2.imshow("OCR Found Textboxes", image)  # Display the image.
        cv2.waitKey(3000)  # 3000 milliseconds.
        cv2.destroyAllWindows()
