// Renderer-level composition constants. Not sourced from `timeline.json`:
// every reel this pipeline produces is a 9:16, 30fps short (Epic 2 Context),
// so these are fixed conventions of the renderer, not per-shot/per-reel data
// (unlike shot timing/transitions/motion below, which is data-driven).
export const FPS = 30;
export const VIDEO_WIDTH = 1080;
export const VIDEO_HEIGHT = 1920;

export type ShotType = "still" | "video";

export type StillEasing = "linear" | "ease" | "easeOut";

// Mirrors `AnimatedStill`'s `StillMotion` prop shape, but with the snake_case
// keys `timeline.json` actually carries (Story 2.2's backfilled
// `VisualPlanContract.Shot.still_motion`, Code Map) -- mapped to `StillMotion`
// at the point of use in `BookReel.tsx`, not renamed here.
export type TimelineStillMotion = {
  scale_from: number;
  scale_to: number;
  translate_x_from?: number;
  translate_x_to?: number;
  translate_y_from?: number;
  translate_y_to?: number;
  easing?: StillEasing;
};

// A single shot exactly as `orchestrator/tools/timeline_converter.py`'s
// `build_timeline_data` emits it -- this type is derived from that real
// output shape, not invented independently (Code Map).
export type TimelineShot = {
  sequence: number;
  type: ShotType;
  src: string;
  start_seconds: number;
  end_seconds: number;
  primary_subtitle_cue_ids: string[];
  fade_in_frames: number;
  fade_out_frames: number;
  // Present on every shot so a video shot can be demoted back to a still;
  // ignored when `type` is "video". Null only in legacy timelines.
  still_motion: TimelineStillMotion | null;
};

export type TimelineData = {
  shots: TimelineShot[];
};

export const toFrame = (seconds: number): number => Math.round(seconds * FPS);
