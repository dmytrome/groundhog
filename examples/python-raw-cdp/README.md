# Raw CDP (Python) example

```bash
pip install -r requirements.txt
python main.py           # or: CDP_URL=http://127.0.0.1:9222 python main.py
```

No browser-automation framework — just the DevTools Protocol over a WebSocket.
It opens a tab via the HTTP endpoint, then drives it with `Page.*` / `Network.*`
commands, setting the User-Agent with `Network.setUserAgentOverride`. This is the
mechanism every higher-level client wraps.
