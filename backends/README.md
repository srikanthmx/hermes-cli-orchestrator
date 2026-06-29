# Bundled Hermes backends

Extra Hermes provider-plugins this tool ships to make media categories work for
**free**. They are not part of the dashboard plugin itself — install them into
Hermes's plugin tree:

## Pollinations (free, keyless image generation)

```bash
cp -r backends/image_gen/pollinations ~/.hermes/plugins/image_gen/pollinations
hermes plugins enable pollinations
hermes config set image_gen.provider pollinations
```

Verified: generates PNGs via `image_gen` with no API key.
