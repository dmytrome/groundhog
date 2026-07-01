# Playwright (Node) example

```bash
npm install
node index.mjs           # or: CDP_URL=http://127.0.0.1:9222 node index.mjs
```

The User-Agent and viewport are set on the context. Use `127.0.0.1`, not
`localhost` — Playwright resolves `localhost` to IPv6 `::1`, which the container
does not listen on.
