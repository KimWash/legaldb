import sys
import json
import argparse
from pathlib import Path

# Setup path so it can import models/com_lock
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

def extract_doc_com(path: Path, max_chars: int) -> dict:
    import win32com.client
    import pythoncom
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        try:
            word.Visible = False
        except Exception:
            pass
        try:
            word.DisplayAlerts = 0  # wdAlertsNone
        except Exception:
            pass
        
        doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)
        raw = doc.Range().Text
        doc.Close(False)
        return {"status": "success", "text": raw}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()

def extract_ppt_com(path: Path, max_chars: int) -> dict:
    import win32com.client
    import pythoncom
    pythoncom.CoInitialize()
    ppt = None
    try:
        ppt = win32com.client.Dispatch("PowerPoint.Application")
        try:
            ppt.Visible = False
        except Exception:
            pass
        
        pres = ppt.Presentations.Open(
            str(path.resolve()),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        lines = []
        slide_count = pres.Slides.Count
        for i in range(1, slide_count + 1):
            slide = pres.Slides(i)
            for j in range(1, slide.Shapes.Count + 1):
                try:
                    shape = slide.Shapes(j)
                    if shape.HasTextFrame:
                        text = shape.TextFrame.TextRange.Text.strip()
                        if text:
                            lines.append(text)
                except Exception:
                    pass
        pres.Close()
        return {"status": "success", "text": "\n".join(lines), "page_count": slide_count}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        if ppt is not None:
            try:
                ppt.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["doc", "ppt"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--max-chars", type=int, default=8000)
    args = parser.parse_args()
    
    path = Path(args.file)
    if args.type == "doc":
        res = extract_doc_com(path, args.max_chars)
    else:
        res = extract_ppt_com(path, args.max_chars)
        
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
