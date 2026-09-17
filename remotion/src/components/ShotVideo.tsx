import React from "react";
import { OffthreadVideo } from "remotion";

type ShotVideoProps = {
  src: string;
};

// A deliberately plain, static video layer: no transform, no scale, no
// translation, no filter animation. Fills the frame with object-fit cover.
//
// Uses `OffthreadVideo` instead of `Video`. `Video` renders via the
// browser's native <video> element, which Remotion seeks and screenshots
// frame-by-frame during render; that seek/paint path can transiently
// mis-scale the decoded picture in Chromium, which is what produced a
// real production zoom/breathing artifact on this reel's video shots
// (reproducible with both a 24fps and a 30fps source, i.e. independent of
// the file itself). `OffthreadVideo` extracts each frame directly instead
// of relying on the browser's video decoder/paint pipeline, avoiding that
// class of artifact entirely. Used for every video-type shot in
// `timeline.json`, whichever shots those are for a given reel.
export const ShotVideo: React.FC<ShotVideoProps> = ({ src }) => {
  return (
    <OffthreadVideo
      src={src}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        transform: "none",
      }}
    />
  );
};
