# Podcast Music Assets

Drop two royalty-free MP3 files in this folder to give the audio summary an
intro sting and an outro bed:

| File | Used for |
|------|----------|
| `assets/intro_music.mp3` | ~3 seconds plays first, then crossfades into the hosts |
| `assets/outro_music.mp3` | fades in under the sign-off at the end |

Both are **optional** — if a file is missing the podcast is still generated,
just without that piece of music. The mixer automatically lowers the music by
**-12 dB** so it sits underneath the voices.

## Where to get free, license-clear music

Use **CC0 / royalty-free** tracks so there are no attribution or copyright
issues:

- **Pixabay Music** — https://pixabay.com/music/ (free, CC0, no attribution)
- **YouTube Audio Library** — https://www.youtube.com/audiolibrary (free; filter
  to "No attribution required")

Pick something short and instrumental (a few seconds of intro is plenty),
download as MP3, and save them here with the exact filenames above.

> These files are intentionally **not** committed to the repo — add your own.
