"""Entry point for running from source and for PyInstaller."""
import multiprocessing
import sys

from data_arranger.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
