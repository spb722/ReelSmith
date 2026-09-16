import React from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";

type ShotTransitionProps = {
  durationInFrames: number;
  /** Fade in from black over this many frames at the start of the shot. */
  fadeInFrames?: number;
  /** Fade out to black over this many frames at the end of the shot. */
  fadeOutFrames?: number;
  /**
   * Optional, very subtle darkening near the very end of the shot (used
   * only for the final shot's reflective close). Does not go fully to
   * black and never touches the subtitle layer, which is rendered
   * separately and stays fully readable.
   */
  endDarkenFrames?: number;
  endDarkenOpacity?: number;
  children: React.ReactNode;
};

// Restrained fade-through-dark used at shot boundaries so cuts feel
// intentional rather than abrupt. Operates purely as an opacity wrapper
// around a shot's existing visual (still or video) inside its own,
// unmodified Sequence -- it never changes shot timing, motion, or
// playback underneath.
export const ShotTransition: React.FC<ShotTransitionProps> = ({
  durationInFrames,
  fadeInFrames = 0,
  fadeOutFrames = 0,
  endDarkenFrames = 0,
  endDarkenOpacity = 1,
  children,
}) => {
  const frame = useCurrentFrame();

  const fadeIn =
    fadeInFrames > 0
      ? interpolate(frame, [0, fadeInFrames], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.inOut(Easing.ease),
        })
      : 1;

  const fadeOutStart = durationInFrames - fadeOutFrames;
  const fadeOut =
    fadeOutFrames > 0
      ? interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.inOut(Easing.ease),
        })
      : 1;

  const endDarkenStart = durationInFrames - endDarkenFrames;
  const endDarken =
    endDarkenFrames > 0
      ? interpolate(frame, [endDarkenStart, durationInFrames], [1, endDarkenOpacity], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.out(Easing.ease),
        })
      : 1;

  const opacity = Math.min(fadeIn, fadeOut, endDarken);

  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};
