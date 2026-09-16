import { Composition } from "remotion";
import { BookReel } from "./BookReel";
import { DURATION_IN_FRAMES, FPS, VIDEO_HEIGHT, VIDEO_WIDTH } from "./timeline";

export const BookReelComposition = () => {
  return (
    <Composition
      id="BookReel"
      component={BookReel}
      durationInFrames={DURATION_IN_FRAMES}
      fps={FPS}
      width={VIDEO_WIDTH}
      height={VIDEO_HEIGHT}
    />
  );
};
