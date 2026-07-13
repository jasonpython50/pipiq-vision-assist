"""Package the addon into a .nvda-addon file (a zip with manifest.ini at the root)."""
import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
	manifest = configparser.ConfigParser()
	with open(os.path.join(ROOT, "manifest.ini"), encoding="utf-8") as f:
		manifest.read_string("[addon]\n" + f.read())
	name = manifest["addon"]["name"]
	version = manifest["addon"]["version"]
	outPath = os.path.join(ROOT, "%s-%s.nvda-addon" % (name, version))
	with zipfile.ZipFile(outPath, "w", zipfile.ZIP_DEFLATED) as z:
		z.write(os.path.join(ROOT, "manifest.ini"), "manifest.ini")
		z.write(os.path.join(ROOT, "readme.md"), "readme.md")
		for sub in ("globalPlugins", "doc"):
			for dirpath, _dirs, files in os.walk(os.path.join(ROOT, sub)):
				for fn in files:
					if fn.endswith((".pyc", ".pyo")):
						continue
					full = os.path.join(dirpath, fn)
					arc = os.path.relpath(full, ROOT).replace(os.sep, "/")
					z.write(full, arc)
	print("built", outPath)


if __name__ == "__main__":
	main()
