import React from "react";
import { AbsoluteFill, Easing, interpolate, interpolateColors, useCurrentFrame } from "remotion";
import { loadFont } from "@remotion/google-fonts/Anton";

// Anton: a condensed, single-weight display face. Its narrow advance width is
// what lets a six- or seven-word phrase sit on one or two lines at this size
// in a 1080px-wide frame, which a normal-width grotesque cannot do. Loaded
// here (rather than relying on a system font stack) so the rendered glyphs are
// identical on every render host.
const { fontFamily } = loadFont();

// Timing is expressed as an offset from the start of the cue's own Sequence,
// so it always resolves within whatever durationInFrames it is given.
const ENTER_DELAY_FRAMES = 4;
const FADE_IN_FRAMES = 8;
const FADE_OUT_FRAMES = 14;

// Per-word highlight: the lit state ramps in just before the word is spoken
// and decays just after, so the emphasis tracks the narrator rather than
// snapping between words.
const HIGHLIGHT_LEAD_FRAMES = 2;
const HIGHLIGHT_TRAIL_FRAMES = 4;
const BASE_COLOR = "#F7F3EA";
const HIGHLIGHT_COLOR = "#FFC24B";

export type ImpactWord = {
  text: string;
  // Both already converted to frames relative to this cue's Sequence by the
  // caller, so this component needs no knowledge of absolute reel time.
  fromFrame: number;
  toFrame: number;
};

type ImpactTypographyProps = {
  words: ImpactWord[];
  durationInFrames: number;
};

// A single, deliberate line of large typography that replaces the ordinary
// subtitle presentation for one cue. The block fades in and out as a whole,
// while each word lights up as it is spoken.
export const ImpactTypography: React.FC<ImpactTypographyProps> = ({ words, durationInFrames }) => {
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
          top: "70%",
          left: "50%",
          width: "82%",
          opacity,
          transform: `translate(-50%, -50%) translateY(${translateY}px) scale(${scale})`,
          textAlign: "center",
          fontFamily,
          fontWeight: 400,
          fontSize: 104,
          letterSpacing: "0.5px",
          lineHeight: 1.12,
          textTransform: "uppercase",
          color: BASE_COLOR,
          textShadow: "0 2px 10px rgba(0, 0, 0, 0.6)",
        }}
      >
        {words.map((word, index) => {
          // Driven entirely by the frame, never a CSS transition, which does
          // not render.
          const lit = interpolate(
            frame,
            [
              word.fromFrame - HIGHLIGHT_LEAD_FRAMES,
              word.fromFrame,
              word.toFrame,
              word.toFrame + HIGHLIGHT_TRAIL_FRAMES,
            ],
            [0, 1, 1, 0],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.out(Easing.quad),
            },
          );

          return (
            <span
              key={`${word.fromFrame}-${index}`}
              style={{
                display: "inline-block",
                marginRight: "0.26em",
                color: interpolateColors(lit, [0, 1], [BASE_COLOR, HIGHLIGHT_COLOR]),
              }}
            >
              {word.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
