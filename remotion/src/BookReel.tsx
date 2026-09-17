import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { AnimatedStill, type StillMotion } from "./components/AnimatedStill";
import { ShotTransition } from "./components/ShotTransition";
import { ShotVideo } from "./components/ShotVideo";
import { Subtitles } from "./components/Subtitles";
import { toFrame, type TimelineData, type TimelineShot, type TimelineStillMotion } from "./timeline";

// Final shot only: a very slight darkening in the closing frames for a
// calm, complete ending. Never reaches full black and never touches the
// subtitle layer, which renders separately and stays fully readable. This
// is a one-off renderer-level closing convention, not per-shot data (Design
// Notes) -- it applies to whichever shot is last in `timeline.json`, so it
// requires no hand-authored change for a new reel.
const FINAL_SHOT_END_DARKEN_FRAMES = 10;
const FINAL_SHOT_END_DARKEN_OPACITY = 0.72;

const toStillMotion = (motion: TimelineStillMotion): StillMotion => ({
  scaleFrom: motion.scale_from,
  scaleTo: motion.scale_to,
  translateXFrom: motion.translate_x_from,
  translateXTo: motion.translate_x_to,
  translateYFrom: motion.translate_y_from,
  translateYTo: motion.translate_y_to,
  easing: motion.easing,
});

// A still shot with no backfilled `still_motion`, or one whose shape is
// malformed (e.g. a missing/non-numeric `scale_from`/`scale_to`), is bad
// data, not a silently-acceptable default (I/O & Edge-Case Matrix: fail
// loudly). `orchestrator/contracts/visual_plan.py`'s `StillMotion` model
// already enforces this shape before `timeline.json` is ever produced, but
// this checks the JSON actually fetched at render time too, since nothing
// stops a differently-produced `timeline.json` from skipping that gate.
const requireStillMotion = (shot: TimelineShot): StillMotion => {
  const motion = shot.still_motion;
  if (!motion || typeof motion.scale_from !== "number" || typeof motion.scale_to !== "number") {
    throw new Error(
      `Shot ${shot.sequence} is type "still" but timeline.json has no valid still_motion `
        + "(scale_from/scale_to) for it.",
    );
  }
  return toStillMotion(motion);
};

export type BookReelProps = {
  // Loaded once by `Composition.tsx`'s `calculateMetadata` and passed down
  // as a prop, rather than fetched again here -- `null` only ever appears as
  // the Studio/preview `defaultProps` placeholder before that resolves.
  timeline: TimelineData | null;
};

export const BookReel: React.FC<BookReelProps> = ({ timeline }) => {
  if (!timeline) {
    return null;
  }

  const lastSequence = Math.max(...timeline.shots.map((shot) => shot.sequence));

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {timeline.shots.map((shot) => {
        const startFrame = toFrame(shot.start_seconds);
        const endFrame = toFrame(shot.end_seconds);
        const durationInFrames = endFrame - startFrame;
        const isFinalShot = shot.sequence === lastSequence;

        let content: React.ReactNode;
        if (shot.type === "still") {
          content = (
            <AnimatedStill
              src={staticFile(shot.src)}
              durationInFrames={durationInFrames}
              motion={requireStillMotion(shot)}
            />
          );
        } else if (shot.type === "video") {
          // All video shots: see ShotVideo.tsx for why this uses
          // OffthreadVideo instead of Video (fixes the render-only
          // zoom/breathing artifact) -- ShotVideoProps carries no transform
          // field, so no code path here can apply one.
          content = <ShotVideo src={staticFile(shot.src)} />;
        } else {
          // Runtime data from `timeline.json` isn't checked by TypeScript's
          // compile-time `ShotType` union -- an unrecognized value must fail
          // loudly, not silently fall through to the video branch.
          throw new Error(
            `Shot ${shot.sequence} has unrecognized type ${JSON.stringify(shot.type)} in `
              + 'timeline.json (expected "still" or "video").',
          );
        }

        return (
          <Sequence key={shot.sequence} from={startFrame} durationInFrames={durationInFrames}>
            <ShotTransition
              durationInFrames={durationInFrames}
              fadeInFrames={shot.fade_in_frames}
              fadeOutFrames={shot.fade_out_frames}
              endDarkenFrames={isFinalShot ? FINAL_SHOT_END_DARKEN_FRAMES : 0}
              endDarkenOpacity={isFinalShot ? FINAL_SHOT_END_DARKEN_OPACITY : 1}
            >
              {content}
            </ShotTransition>
          </Sequence>
        );
      })}
      <Subtitles />
      <Audio src={staticFile("audio/narration.wav")} />
    </AbsoluteFill>
  );
};
