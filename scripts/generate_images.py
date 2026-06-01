"""
DEPRECATED — AI image generation has been removed.

Artist images are now real placeholder portraits fetched into object storage by
scripts/fetch_artist_images.py. This shim just forwards there so any old
reference keeps working.
"""
import runpy
import os

if __name__ == "__main__":
    print("generate_images.py is deprecated — running fetch_artist_images.py instead.")
    runpy.run_path(os.path.join(os.path.dirname(__file__), "fetch_artist_images.py"),
                   run_name="__main__")
