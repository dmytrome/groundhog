# chromedp (Go) example

```bash
go mod tidy
go run .                 # or: CDP_URL=http://127.0.0.1:9222 go run .
```

Resolves the browser WebSocket URL from `/json/version`, attaches with
`chromedp.NewRemoteAllocator`, and sets the User-Agent via
`emulation.SetUserAgentOverride`. `go mod tidy` pulls in `cdproto`.
