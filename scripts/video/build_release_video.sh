#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
output_dir="$repo_root/output/release-video"
playwright_dir="$output_dir/playwright"
proof_mp4="$output_dir/proof.mp4"
title_mp4="$output_dir/title.mp4"
end_mp4="$output_dir/end.mp4"
concat_list="$output_dir/concat.txt"
final_mp4="$output_dir/relaypay-v0.1.0-proof.mp4"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }
mkdir -p "$output_dir"
rm -rf "$playwright_dir"

cd "$repo_root/apps/console"
npx playwright test --config playwright.video.config.ts
node scripts/render-release-assets.mjs "$output_dir"

recording="$(find "$playwright_dir" -name video.webm -type f -print -quit)"
test -n "$recording" || { echo "Playwright did not produce video.webm" >&2; exit 1; }

duration="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$recording")"
first_cut="$(awk -v duration="$duration" 'BEGIN { printf "%.3f", duration / 3 }')"
second_cut="$(awk -v duration="$duration" 'BEGIN { printf "%.3f", duration * 2 / 3 }')"

ffmpeg -y -loop 1 -i "$output_dir/title.png" -t 4 -r 30 -c:v libx264 -pix_fmt yuv420p "$title_mp4"
ffmpeg -y -i "$recording" \
  -loop 1 -i "$output_dir/caption-1.png" \
  -loop 1 -i "$output_dir/caption-2.png" \
  -loop 1 -i "$output_dir/caption-3.png" \
  -filter_complex "[0:v]scale=1440:900,fps=30[base];[base][1:v]overlay=0:0:enable='between(t,0,$first_cut)'[one];[one][2:v]overlay=0:0:enable='between(t,$first_cut,$second_cut)'[two];[two][3:v]overlay=0:0:enable='between(t,$second_cut,$duration)'[video]" \
  -map "[video]" -t "$duration" -an -c:v libx264 -pix_fmt yuv420p "$proof_mp4"
ffmpeg -y -loop 1 -i "$output_dir/end.png" -t 5 -r 30 -c:v libx264 -pix_fmt yuv420p "$end_mp4"

printf "file '%s'\nfile '%s'\nfile '%s'\n" "$title_mp4" "$proof_mp4" "$end_mp4" > "$concat_list"
ffmpeg -y -f concat -safe 0 -i "$concat_list" \
  -c copy -movflags +faststart "$final_mp4"

printf 'Created %s\n' "$final_mp4"
