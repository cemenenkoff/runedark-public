![Platform: Windows](https://img.shields.io/badge/platform-windows-blue)
![Python Version](https://img.shields.io/badge/python-3.10.9-blue)
![Code Style](https://img.shields.io/badge/code%20style-black-000000)

![logo](src/img/ui/splash.png)
- RuneDark is a desktop client for managing automation scripts in games.

# Features
- Unlike traditional injection or reflection frameworks, RuneDark takes a hands-off approach, leveraging computer vision and optical character recognition for precise and efficient automation.

  - ***Object Detection***: Detects and converts in-game objects into data structures.
  - ***Image Recognition***: Identifies images within images using computer vision.
  - ***Color-on-Color OCR***: Reads text on varying font and background colors reliably.
  - ***Humanization***: Adds randomness to mouse movements, wait times, and keystrokes for natural behavior.

# Quickstart <img height=20 src="doc/img/windows-logo.png"/>
1. Install [Python 3.10.9](https://www.python.org/downloads/release/python-3109/).
2. Install [Git Bash for Windows](https://git-scm.com/downloads).
3. Open an IDE (e.g. VS Code).
4. Clone this repository.
5. Set up a virtual environment.
   1. Ensure `virtualenv` is installed: `pip install virtualenv`
   2. Create a virtual environment: `virtualenv venv --python=python3.10.9`
   3. Activate the newly-created virtual environment: `source venv/Scripts/activate`
   4. Install dependencies: `pip install -r requirements.txt`
6. Run: `python src/rune_dark.py`

❌ If you are getting `ModuleNotFound` errors, *restart* your IDE for the newly-installed modules to be recognized.

# More Info
- Explore detailed tutorials and guides in the [wiki](doc/WIKI.md).
