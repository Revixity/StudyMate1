"""StudyMate P01. Run with: uv run prototype1.py"""
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["fastapi", "uvicorn", "python-multipart", "pypdf", "httpx", "python-dotenv"]
# ///
import argparse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StudyMate: answers from your course material")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    from backend.main import app

    print(f"StudyMate is starting at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
