import os
import re
import csv
from datetime import datetime

# Keywords to search for
KEYWORDS = [
    "Mattern", "Holst", "Baulast", "Außenanlage", "Bauunternehm", 
    "Grundstück", "bauhabenbezogen", "vorhabenbezogen", "Kosten", "35", "Vereinbarung", "Absprache",
    "Schaaf", "Möller"
]

def search_files():
    output_dir = "output"
    results = []
    
    for root, dirs, files in os.walk(output_dir):
        for file in files:
            if file.endswith(".txt") or file.endswith(".eml"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    try:
                        with open(path, "r", encoding="iso-8859-1") as f:
                            content = f.read()
                    except:
                        continue
                        
                # Extract basic metadata from our .txt format
                date_match = re.search(r"Date:\s*(.+)", content)
                subject_match = re.search(r"Subject:\s*(.+)", content)
                from_match = re.search(r"From:\s*(.+)", content)
                
                date = date_match.group(1).strip() if date_match else "Unknown"
                subject = subject_match.group(1).strip() if subject_match else "Unknown"
                sender = from_match.group(1).strip() if from_match else "Unknown"
                
                # Check if it contains keywords
                body = content.lower()
                matched_keywords = [kw for kw in KEYWORDS if kw.lower() in body]
                
                if matched_keywords:
                    # Extract surrounding context for each keyword
                    snippets = {}
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        for kw in matched_keywords:
                            if kw.lower() in line.lower():
                                start = max(0, i - 2)
                                end = min(len(lines), i + 3)
                                snippet = "\n".join(lines[start:end])
                                if kw not in snippets:
                                    snippets[kw] = []
                                snippets[kw].append(snippet)
                                
                    results.append({
                        "file": path,
                        "date": date,
                        "subject": subject,
                        "from": sender,
                        "keywords": matched_keywords,
                        "snippets": snippets
                    })
                    
    # Sort results by date
    results.sort(key=lambda x: x["date"])
    
    with open("analysis_report.txt", "w", encoding="utf-8") as out:
        for r in results:
            out.write(f"--- FILE: {r['file']} ---\n")
            out.write(f"Date: {r['date']}\n")
            out.write(f"From: {r['from']}\n")
            out.write(f"Subject: {r['subject']}\n")
            out.write(f"Keywords: {', '.join(r['keywords'])}\n\n")
            for kw, snips in r['snippets'].items():
                out.write(f"  [{kw}]\n")
                for snip in snips[:3]: # Limit to 3 snippets per keyword per file
                    out.write(f"    {snip.replace(chr(10), chr(10)+'    ')}\n")
                out.write("\n")

if __name__ == "__main__":
    search_files()
    print("Extraction complete.")
