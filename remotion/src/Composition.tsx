import { Composition, staticFile } from "remotion";
import { BookReel, type BookReelProps } from "./BookReel";
import { FPS, VIDEO_HEIGHT, VIDEO_WIDTH, toFrame, type TimelineData } from "./timeline";

// Data-driven duration: `timeline.json`'s own shot list is the only source
// of truth for how long the reel is (Code Map) -- fps/dimensions stay fixed
// renderer conventions (`timeline.ts`). No `delayRender` here:
// `calculateMetadata` already returns a `Promise`, so awaiting `fetch`
// directly is enough (unlike a React component's render, which needs
// `delayRender`/`continueRender` to pause for async work). A thrown/rejected
// fetch here fails the render loudly, as required (I/O & Edge-Case Matrix).
//
// The fetched `timeline` is also returned as `props`, so `BookReel` consumes
// it directly instead of fetching `timeline.json` a second time itself.
const calculateMetadata = async () => {
  const response = await fetch(staticFile("data/timeline.json"));
  const timeline: TimelineData = await response.json();

  if (!timeline.shots || timeline.shots.length === 0) {
    throw new Error("remotion/public/data/timeline.json has no shots.");
  }

  const lastEndSeconds = Math.max(...timeline.shots.map((shot) => shot.end_seconds));

  return {
    durationInFrames: toFrame(lastEndSeconds),
    fps: FPS,
    width: VIDEO_WIDTH,
    height: VIDEO_HEIGHT,
    props: { timeline },
  };
};

const defaultProps: BookReelProps = { timeline: null };

export const BookReelComposition = () => {
  return (
    <Composition
      id="BookReel"
      component={BookReel}
      calculateMetadata={calculateMetadata}
      defaultProps={defaultProps}
    />
  );
};
