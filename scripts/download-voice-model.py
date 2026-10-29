"""Download the small offline Vosk models Luna listens with.

Usage: download-voice-model.py [en] [sv] ...   (default: en). Each language pack names its
model (luna_core/languages/<code>.json, "listening_model"). Luna listens in the language of the
selected voice; without that language's model she keeps listening in English.
"""
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from luna_core.voice import MODELS, model_path  # noqa: E402

URL = "https://alphacephei.com/vosk/models/%s.zip"


def download(language):
    name = MODELS[language]
    target = model_path(language)
    if target.is_dir():
        print("Model already installed:", target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / (name + ".zip")
    print("Downloading", URL % name)
    urllib.request.urlretrieve(URL % name, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target.parent)
    archive.unlink()
    print("Installed:", target)


def main(languages):
    unknown = [code for code in languages if code not in MODELS]
    if unknown:
        sys.exit("Unknown language %s; choose from %s" % (", ".join(unknown), ", ".join(MODELS)))
    for language in languages or ["en"]:
        download(language)


if __name__ == "__main__":
    main(sys.argv[1:])
