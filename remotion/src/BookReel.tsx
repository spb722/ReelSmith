import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { AnimatedStill, type StillMotion } from "./components/AnimatedStill";
import { ShotTransition } from "./components/ShotTransition";
import { ShotVideo } from "./components/ShotVideo";
import { Subtitles } from "./components/Subtitles";
import { shots } from "./timeline";

// Restrained fade-through-dark transitions at each shot boundary, sized in
// frames (~4-8, "approximately 4-8 frames"). Chosen per pair rather than
// applied uniformly:
// 1->2 subtle fade (preserves the breathing room after "Don't try.")
// 2->3 gentle dissolve, 3->4 very restrained, 4->5 clean cut (direct),
// 5->6 very short, 6->7 gentle, 7->8 soft into the final reflective shot.
const SHOT_TRANSITIONS: Record<number, { fadeInFrames: number; fadeOutFrames: number }> = {
  1: { fadeInFrames: 0, fadeOutFrames: 6 },
  2: { fadeInFrames: 6, fadeOutFrames: 6 },
  3: { fadeInFrames: 6, fadeOutFrames: 4 },
  4: { fadeInFrames: 4, fadeOutFrames: 0 },
  5: { fadeInFrames: 0, fadeOutFrames: 4 },
  6: { fadeInFrames: 4, fadeOutFrames: 6 },
  7: { fadeInFrames: 6, fadeOutFrames: 8 },
  8: { fadeInFrames: 8, fadeOutFrames: 0 },
};

// Final shot only: a very slight darkening in the closing frames for a
// calm, complete ending. Never reaches full black and never touches the
// subtitle layer, which renders separately and stays fully readable.
const FINAL_SHOT_ID = 8;
const FINAL_SHOT_END_DARKEN_FRAMES = 10;
const FINAL_SHOT_END_DARKEN_OPACITY = 0.72;

// Motion presets for the still shots only (Veo video shots 2, 5, 6 are
// untouched). Keyed by shot id from timeline.ts, which is not modified.
const STILL_MOTION: Record<number, StillMotion> = {
  // Shot 1: very slow, deliberate push-in. Almost no translation.
  1: {
    scaleFrom: 1.0,
    scaleTo: 1.07,
    easing: "linear",
  },
  // Shot 3: gentle upward drift, softer than shot 1.
  3: {
    scaleFrom: 1.02,
    scaleTo: 1.07,
    translateYFrom: 0,
    translateYTo: -2,
    easing: "ease",
  },
  // Shot 4: slow push-in with a small horizontal move, explanatory feel.
  4: {
    scaleFrom: 1.0,
    scaleTo: 1.06,
    translateXFrom: 0,
    translateXTo: 1.5,
    easing: "ease",
  },
  // Shot 7: subtle lateral drift across the horizon, very small scale change.
  7: {
    scaleFrom: 1.02,
    scaleTo: 1.04,
    translateXFrom: 0,
    translateXTo: 1.2,
    easing: "ease",
  },
  // Shot 8: extremely slow, almost imperceptible push, settles for the ending.
  8: {
    scaleFrom: 1.0,
    scaleTo: 1.015,
    easing: "easeOut",
  },
};

export const BookReel: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {shots.map((shot) => {
        const durationInFrames = shot.endFrame - shot.startFrame;
        const transition = SHOT_TRANSITIONS[shot.id];
        const isFinalShot = shot.id === FINAL_SHOT_ID;

        return (
          <Sequence key={shot.id} from={shot.startFrame} durationInFrames={durationInFrames}>
            <ShotTransition
              durationInFrames={durationInFrames}
              fadeInFrames={transition?.fadeInFrames ?? 0}
              fadeOutFrames={transition?.fadeOutFrames ?? 0}
              endDarkenFrames={isFinalShot ? FINAL_SHOT_END_DARKEN_FRAMES : 0}
              endDarkenOpacity={isFinalShot ? FINAL_SHOT_END_DARKEN_OPACITY : 1}
            >
              {shot.type === "still" ? (
                <AnimatedStill
                  src={shot.src}
                  durationInFrames={durationInFrames}
                  motion={STILL_MOTION[shot.id]}
                />
              ) : (
                // All video shots (2, 5, 6): see ShotVideo.tsx for why this
                // uses OffthreadVideo instead of Video (fixes the
                // render-only zoom/breathing artifact, originally found and
                // fixed on Shot 2, now applied to Shots 5 and 6 too).
                <ShotVideo src={shot.src} />
              )}
            </ShotTransition>
          </Sequence>
        );
      })}
      <Subtitles />
      <Audio src={staticFile("audio/narration.wav")} />
    </AbsoluteFill>
  );
};
