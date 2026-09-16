import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, useCurrentFrame } from "remotion";

export type StillEasing = "linear" | "ease" | "easeOut";

export type StillMotion = {
  scaleFrom: number;
  scaleTo: number;
  translateXFrom?: number;
  translateXTo?: number;
  translateYFrom?: number;
  translateYTo?: number;
  easing?: StillEasing;
};

const EASINGS: Record<StillEasing, (t: number) => number> = {
  linear: Easing.linear,
  ease: Easing.inOut(Easing.ease),
  easeOut: Easing.out(Easing.quad),
};

type AnimatedStillProps = {
  src: string;
  durationInFrames: number;
  motion: StillMotion;
};

// Ken-Burns-style still animation. Scale and translation are driven by the
// same [0,1] progress value so translation always stays within the extra
// margin the scale-up provides, guaranteeing full frame coverage.
export const AnimatedStill: React.FC<AnimatedStillProps> = ({ src, durationInFrames, motion }) => {
  const frame = useCurrentFrame();
  const {
    scaleFrom,
    scaleTo,
    translateXFrom = 0,
    translateXTo = 0,
    translateYFrom = 0,
    translateYTo = 0,
    easing = "ease",
  } = motion;

  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASINGS[easing],
  });

  const scale = interpolate(progress, [0, 1], [scaleFrom, scaleTo]);
  const translateX = interpolate(progress, [0, 1], [translateXFrom, translateXTo]);
  const translateY = interpolate(progress, [0, 1], [translateYFrom, translateYTo]);

  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <Img
        src={src}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale}) translate(${translateX}%, ${translateY}%)`,
          transformOrigin: "center center",
        }}
      />
    </AbsoluteFill>
  );
};
