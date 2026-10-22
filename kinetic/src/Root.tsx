import { Composition } from "remotion";
import { calculateMetadata, PropsSchema } from "./data";
import { KineticShort } from "./KineticShort";
import { FPS, HEIGHT, WIDTH } from "./lib/motion";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="KineticShort"
      component={KineticShort}
      schema={PropsSchema}
      calculateMetadata={calculateMetadata}
      durationInFrames={FPS * 30}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={{ id: "did-you-know", showSafeZones: false, mask: false }}
    />
  );
};
