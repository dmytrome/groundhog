// chromedp (Go) over CDP. The container does not set a User-Agent, so we set it
// with emulation.SetUserAgentOverride.
//
//	go mod tidy
//	go run .
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

	"github.com/chromedp/cdproto/emulation"
	"github.com/chromedp/chromedp"
)

const realUA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"

// browserWS resolves the browser-level WebSocket URL from the HTTP endpoint.
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
	var ua string
	if err := chromedp.Run(ctx,
		emulation.SetUserAgentOverride(realUA),
		chromedp.Navigate("https://bot.sannysoft.com/"),
		chromedp.Sleep(5*time.Second),
		chromedp.Evaluate(`navigator.userAgent`, &ua),
		chromedp.CaptureScreenshot(&buf),
	); err != nil {
		log.Fatal(err)
	}
	if err := os.WriteFile("sannysoft.png", buf, 0o644); err != nil {
		log.Fatal(err)
	}
	fmt.Println("saved sannysoft.png — UA:", ua)
}
