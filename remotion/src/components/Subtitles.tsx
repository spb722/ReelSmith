import React, { useCallback, useEffect, useState } from "react";
import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useDelayRender,
  useVideoConfig,
} from "remotion";
import { ImpactTypography } from "./ImpactTypography";

// Style hints as authored in public/data/subtitle_cues.json. Do not add
// labels here that are not present in the source file.
type StyleHint = "NORMAL" | "IMPACT" | "EMPHASIS" | "REFLECTION";

type SubtitleCue = {
  cue_id: string;
  start_seconds: number;
  end_seconds: number;
  text: string;
  style_hint: StyleHint;
};

type SubtitleCuesFile = {
  cues: SubtitleCue[];
};

const toFrame = (seconds: number, fps: number): number => Math.round(seconds * fps);

const FADE_IN_FRAMES = 7;

const VARIANTS: Record<StyleHint, { container: React.CSSProperties; initialScale: number; initialTranslateY: number }> = {
  NORMAL: {
    container: {
      fontSize: 44,
      fontWeight: 600,
      letterSpacing: "0.2px",
      padding: "14px 30px",
      borderRadius: 14,
      background: "rgba(0, 0, 0, 0.45)",
      boxShadow: "0 4px 16px rgba(0, 0, 0, 0.5)",
    },
    initialScale: 0.96,
    initialTranslateY: 10,
  },
  EMPHASIS: {
    container: {
      fontSize: 46,
      fontWeight: 700,
      letterSpacing: "0.3px",
      padding: "14px 30px",
      borderRadius: 14,
      background: "rgba(0, 0, 0, 0.5)",
      boxShadow: "0 4px 18px rgba(0, 0, 0, 0.55)",
    },
    initialScale: 0.96,
    initialTranslateY: 10,
  },
  IMPACT: {
    container: {
      fontSize: 56,
      fontWeight: 800,
      letterSpacing: "0.5px",
      padding: "18px 36px",
      borderRadius: 18,
      background: "rgba(0, 0, 0, 0.55)",
      boxShadow: "0 6px 22px rgba(0, 0, 0, 0.65)",
    },
    initialScale: 0.9,
    initialTranslateY: 14,
  },
  REFLECTION: {
    container: {
      fontSize: 42,
      fontWeight: 500,
      letterSpacing: "0.2px",
      padding: "12px 28px",
      borderRadius: 14,
      background: "rgba(0, 0, 0, 0.4)",
      boxShadow: "0 4px 16px rgba(0, 0, 0, 0.45)",
    },
    initialScale: 0.96,
    initialTranslateY: 10,
  },
};

const SubtitleCard: React.FC<{ text: string; styleHint: StyleHint }> = ({ text, styleHint }) => {
  const frame = useCurrentFrame();
  const variant = VARIANTS[styleHint] ?? VARIANTS.NORMAL;

  const progress = interpolate(frame, [0, FADE_IN_FRAMES], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const translateY = interpolate(progress, [0, 1], [variant.initialTranslateY, 0]);
  const scale = interpolate(progress, [0, 1], [variant.initialScale, 1]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 200,
      }}
    >
      <div
        style={{
          opacity: progress,
          transform: `translateY(${translateY}px) scale(${scale})`,
          maxWidth: "85%",
          textAlign: "center",
          fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
          color: "rgba(255, 255, 255, 0.96)",
          textShadow: "0 2px 10px rgba(0, 0, 0, 0.6)",
          lineHeight: 1.3,
          ...variant.container,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

export const Subtitles: React.FC = () => {
  const { fps } = useVideoConfig();
  const [cues, setCues] = useState<SubtitleCue[] | null>(null);
  const { delayRender, continueRender, cancelRender } = useDelayRender();
  const [handle] = useState(() => delayRender("Loading subtitle cues"));

  const fetchCues = useCallback(async () => {
    try {
      const response = await fetch(staticFile("data/subtitle_cues.json"));
      const data: SubtitleCuesFile = await response.json();
      setCues(data.cues);
      continueRender(handle);
    } catch (e) {
      cancelRender(e);
    }
  }, [continueRender, cancelRender, handle]);

  useEffect(() => {
    fetchCues();
  }, [fetchCues]);

  if (!cues) {
    return null;
  }

  return (
    <AbsoluteFill>
      {cues.map((cue) => {
        const startFrame = toFrame(cue.start_seconds, fps);
        const endFrame = toFrame(cue.end_seconds, fps);
        const durationInFrames = endFrame - startFrame;

        if (durationInFrames <= 0) {
          return null;
        }

        return (
          <Sequence key={cue.cue_id} from={startFrame} durationInFrames={durationInFrames}>
            {cue.style_hint === "IMPACT" ? (
              // Impact cues get the special large reveal instead of the
              // ordinary subtitle card, so the phrase is not shown twice.
              <ImpactTypography text={cue.text} durationInFrames={durationInFrames} />
            ) : (
              <SubtitleCard text={cue.text} styleHint={cue.style_hint} />
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
