"""Download the small offline English Vosk model used for voice commands."""
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from robot_hud.voice import MODEL_NAME, model_path  # noqa: E402

URL = "https://alphacephei.com/vosk/models/%s.zip" % MODEL_NAME


def main():
    target = model_path()
    if target.is_dir():
        print("Model already installed:", target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / (MODEL_NAME + ".zip")
    print("Downloading", URL)
    urllib.request.urlretrieve(URL, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target.parent)
    archive.unlink()
    print("Installed:", target)


if __name__ == "__main__":
    main()
