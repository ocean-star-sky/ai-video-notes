"""ai-video-notes VPS pipeline.

Mac (residential IP) fetches channel listings and subtitles via ssh; the VPS
summarizes with an LLM, groups videos into topic threads, renders the static
site and publishes it.  See /root/.claude/plans/https-ocean-star-sky-github-io-ai-video-majestic-otter.md
"""
