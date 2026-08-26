package main

import (
	"fmt"
	"os"
	"strings"
)

// wordCount returns the number of whitespace-separated words in s.
func wordCount(s string) int {
	return len(strings.Fields(s))
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: main <text>")
		os.Exit(1)
	}
	fmt.Println(wordCount(strings.Join(os.Args[1:], " ")))
}
