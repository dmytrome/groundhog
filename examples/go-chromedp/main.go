// chromedp (Go) over CDP.
//
//	go mod tidy
//	go run .
//
// Caveat: chromedp enables the CDP Runtime domain (for Evaluate), so a detector
// flags it as automated (isAutomatedWithCDP) — there is no supported way to stop
// chromedp from doing so. For a FULL pass in Go, drive raw CDP over a WebSocket
// without enabling Runtime (the same approach as ../python-raw-cdp). The
// container's fingerprint stealth (UA, WebGL, timezone) applies either way.
//
// CDP_URL defaults to http://127.0.0.1:9222.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/chromedp/chromedp"
)

func browserWS(base string) (string, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(base + "/json/version")
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var v struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&v); err != nil {
		return "", err
	}
	return v.WebSocketDebuggerURL, nil
}

func main() {
	base := os.Getenv("CDP_URL")
	if base == "" {
		base = "http://127.0.0.1:9222"
	}
	wsURL, err := browserWS(base)
	if err != nil {
		log.Fatal(err)
	}

	allocCtx, cancelAlloc := chromedp.NewRemoteAllocator(context.Background(), wsURL)
	defer cancelAlloc()
	ctx, cancel := chromedp.NewContext(allocCtx)
	defer cancel()
	ctx, cancelTimeout := context.WithTimeout(ctx, 60*time.Second)
	defer cancelTimeout()

	var buf []byte
	if err := chromedp.Run(ctx,
		chromedp.Navigate("https://deviceandbrowserinfo.com/are_you_a_bot"),
		chromedp.Sleep(6*time.Second),
		chromedp.CaptureScreenshot(&buf),
	); err != nil {
		log.Fatal(err)
	}
	if err := os.WriteFile("result.png", buf, 0o644); err != nil {
		log.Fatal(err)
	}
	fmt.Println("saved result.png")
}
