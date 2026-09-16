import React from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";

// Timing is expressed as an offset from the start of the cue's own Sequence,
// so it always resolves within whatever durationInFrames it is given.
const ENTER_DELAY_FRAMES = 4;
const FADE_IN_FRAMES = 8;
const FADE_OUT_FRAMES = 14;

type ImpactTypographyProps = {
  text: string;
  durationInFrames: number;
};

// A single, deliberate line of large typography that replaces the ordinary
// subtitle presentation for one cue. No per-letter/word animation.
export const ImpactTypography: React.FC<ImpactTypographyProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();

  const fadeInEnd = ENTER_DELAY_FRAMES + FADE_IN_FRAMES;
  const fadeOutStart = Math.max(fadeInEnd, durationInFrames - FADE_OUT_FRAMES);

  const opacity = interpolate(
    frame,
    [ENTER_DELAY_FRAMES, fadeInEnd, fadeOutStart, durationInFrames],
    [0, 1, 1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.ease),
    },
  );

  const entrance = interpolate(frame, [ENTER_DELAY_FRAMES, fadeInEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.quad),
  });

  const scale = interpolate(entrance, [0, 1], [0.96, 1]);
  const translateY = interpolate(entrance, [0, 1], [10, 0]);

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          top: "56%",
          left: "50%",
          width: "82%",
          opacity,
          transform: `translate(-50%, -50%) translateY(${translateY}px) scale(${scale})`,
          textAlign: "center",
          fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
          fontWeight: 800,
          fontSize: 100,
          letterSpacing: "0.5px",
          lineHeight: 1.15,
          color: "#F7F3EA",
          textShadow: "0 2px 10px rgba(0, 0, 0, 0.6)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
