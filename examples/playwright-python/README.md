# Playwright (Python) example

```bash
pip install -r requirements.txt
python main.py           # or: CDP_URL=http://127.0.0.1:9222 python main.py
```

The User-Agent and viewport are set on the context. No `playwright install` is
needed — we connect to the container's Chrome, not a local browser. Use
`127.0.0.1`, not `localhost`.
