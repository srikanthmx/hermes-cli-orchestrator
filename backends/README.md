# Bundled Hermes backends

Extra Hermes provider-plugins this tool ships to make media categories work.
They are not part of the dashboard plugin itself — install them into Hermes's
plugin tree.

## Pollinations — free, keyless image generation ✅ verified

```bash
cp -r backends/image_gen/pollinations ~/.hermes/plugins/image_gen/pollinations
hermes plugins enable pollinations
hermes config set image_gen.provider pollinations
```
Verified: generates PNGs via Hermes's `image_gen` registry with no API key.

## fal — text-to-video ⚙️ needs FAL_KEY (unverified)

```bash
cp -r backends/video_gen/fal ~/.hermes/plugins/video_gen/fal
hermes plugins enable video_gen/fal
hermes config set video_gen.provider fal
# add a free-trial FAL_KEY in the CLI Matrix → Media panel
```
Uses fal's REST queue API (no `fal_client` dependency). Verified: imports,
ABC compliance, and registration into Hermes's `video_gen` registry. **Not**
verified: actual generation — run it once a `FAL_KEY` is set to confirm the
request/response shapes.
