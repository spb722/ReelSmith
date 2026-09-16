import { staticFile } from "remotion";

export const FPS = 30;
export const VIDEO_WIDTH = 1080;
export const VIDEO_HEIGHT = 1920;
export const DURATION_IN_FRAMES = 1573;

export type ShotType = "still" | "video";

export type Shot = {
  id: number;
  type: ShotType;
  src: string;
  startFrame: number;
  endFrame: number;
};

type ShotDefinition = {
  id: number;
  type: ShotType;
  src: string;
  startSeconds: number;
  endSeconds: number;
};

const toFrame = (seconds: number): number => Math.round(seconds * FPS);

const shotDefinitions: ShotDefinition[] = [
  {
    id: 1,
    type: "still",
    src: staticFile("stills/shot_01.png"),
    startSeconds: 0,
    endSeconds: 12.0,
  },
  {
    id: 2,
    type: "video",
    src: staticFile("video/shot_02.mp4"),
    startSeconds: 12.0,
    endSeconds: 18.5,
  },
  {
    id: 3,
    type: "still",
    src: staticFile("stills/shot_03.png"),
    startSeconds: 18.5,
    endSeconds: 24.5,
  },
  {
    id: 4,
    type: "still",
    src: staticFile("stills/shot_04.png"),
    startSeconds: 24.5,
    endSeconds: 33.8,
  },
  {
    id: 5,
    type: "video",
    src: staticFile("video/shot_05.mp4"),
    startSeconds: 33.8,
    endSeconds: 38.4,
  },
  {
    id: 6,
    type: "video",
    src: staticFile("video/shot_06.mp4"),
    startSeconds: 38.4,
    endSeconds: 45.0,
  },
  {
    id: 7,
    type: "still",
    src: staticFile("stills/shot_07.png"),
    startSeconds: 45.0,
    endSeconds: 49.6,
  },
  {
    id: 8,
    type: "still",
    src: staticFile("stills/shot_08.png"),
    startSeconds: 49.6,
    endSeconds: 52.44,
  },
];

export const shots: Shot[] = shotDefinitions.map((shot) => ({
  id: shot.id,
  type: shot.type,
  src: shot.src,
  startFrame: toFrame(shot.startSeconds),
  endFrame: toFrame(shot.endSeconds),
}));
