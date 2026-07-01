# Selenium (Python) example

```bash
pip install -r requirements.txt
python main.py           # or: CDP_ADDRESS=127.0.0.1:9222 python main.py
```

Selenium attaches to the container's Chrome via the `debuggerAddress` option
rather than launching its own browser, and sets the User-Agent with a raw CDP
command (`Network.setUserAgentOverride`).

Selenium still needs a `chromedriver` matching the container's Chrome version;
Selenium Manager (built into Selenium 4) fetches it automatically.
