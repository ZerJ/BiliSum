import assert from "node:assert/strict";

import { buildCachedVideoMediaUrl, buildPlayerEmbedDescriptor, buildYouTubeEmbedUrl, withPlayerSeek } from "../src/videoPlayer.ts";

function run(name: string, fn: () => void) {
  fn();
  console.log(`ok - ${name}`);
}

run("builds youtube embed urls from watch and short links", () => {
  assert.equal(
    buildYouTubeEmbedUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123"),
    "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=0&rel=0",
  );
  assert.equal(
    buildYouTubeEmbedUrl("https://youtu.be/dQw4w9WgXcQ?t=43"),
    "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=0&rel=0",
  );
});

run("adds youtube seek parameters with start seconds", () => {
  const embedUrl = buildYouTubeEmbedUrl("https://www.youtube.com/shorts/dQw4w9WgXcQ");
  assert.ok(embedUrl);
  assert.equal(
    withPlayerSeek(embedUrl!, "youtube", 95, 3),
    "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=0&rel=0&start=95&_ts=3",
  );
});

run("returns descriptor labels for supported player platforms", () => {
  const bilibili = buildPlayerEmbedDescriptor("https://www.bilibili.com/video/BV1xx411c7mD?p=2");
  const youtube = buildPlayerEmbedDescriptor("https://youtu.be/dQw4w9WgXcQ");

  assert.equal(bilibili?.platform, "bilibili");
  assert.equal(bilibili?.openLabel, "在 Bilibili 打开");
  assert.equal(youtube?.platform, "youtube");
  assert.equal(youtube?.openLabel, "在 YouTube 打开");
});

run("builds seekable cached media urls for douyin and local videos", () => {
  assert.equal(
    buildCachedVideoMediaUrl(
      "douyin-video",
      "douyin",
      "https://www.douyin.com/video/6961737553342991651",
      "completed",
    ),
    "/api/v1/videos/douyin-video/media?status=completed",
  );
  assert.equal(
    buildCachedVideoMediaUrl("local-video", "local", "C:\\Videos\\demo.mp4", "running"),
    "/api/v1/videos/local-video/media?status=running",
  );
  assert.equal(buildCachedVideoMediaUrl("youtube-video", "youtube", "https://youtu.be/example"), null);
  assert.equal(buildCachedVideoMediaUrl("audio", "local", "C:\\Audio\\demo.mp3"), null);
});
