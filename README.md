# EML to Files

Batch-converts a folder of `.eml` email exports into clean, readable text files with extracted attachments.

## Download

Grab the latest build for your platform from the
[Releases page](../../releases/latest):

| Platform | File |
|---|---|
| Windows | `eml-to-files-windows.exe` |
| macOS | `eml-to-files-macos` |
| Linux | `eml-to-files-linux` |

Double-click to run — no installation, no terminal, no config files needed.

> **macOS note:** the first time you open it, right-click the file and choose
> *Open* to bypass Gatekeeper, then click *Open* in the dialog.
>
> **Linux note:** you may need to `chmod +x eml-to-files-linux` first.

## What it does

1. **Parses** each `.eml` (headers, multipart MIME body, attachments)
2. **Cleans** the message text:
   - Strips email signatures
   - Removes quoted reply chains (keeps only the newest message)
   - Converts HTML-only emails to plain text
   - Removes confidentiality disclaimers
   - Strips meaningless `[cid:...]` inline-image references
   - Normalises whitespace
3. **Extracts** file attachments (PDFs, images, documents)
4. **Writes** a `message.txt` + `attachments/` folder per email
5. **Generates** an `index.csv` catalogue of all processed messages

## Output structure

```
output/
├── index.csv
├── 2022/
│   └── 2022-05-30_project-update/
│       ├── message.txt
│       └── attachments/
│           └── Report.pdf
├── 2024/
│   └── ...
```

## Running from source

If you prefer to run the Python source directly:

```bash
pip install -r requirements.txt
python main.py            # opens the GUI
python main.py --help     # CLI mode
```

### CLI options

```
python main.py                        # GUI
python main.py -c config.yaml         # CLI with config file
python main.py --dry-run              # preview without writing
python main.py --workers 1            # single-threaded (for debugging)
```

All CLI settings can be placed in a `config.yaml` file — see
[config.yaml](config.yaml) for the full list with comments.

## Requirements (source only)

- Python 3.10+
- `customtkinter` (GUI)
- `pyyaml` (CLI config files — optional)

## License

MIT
