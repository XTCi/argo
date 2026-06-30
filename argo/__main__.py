import asyncio
import sys
from argo.main import main

if __name__ == "__main__":
    yolo = "--yolo" in sys.argv
    asyncio.run(main(yolo=yolo))
