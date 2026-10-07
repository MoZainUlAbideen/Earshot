import { SoundRibbon } from "./sound-ribbon";

/** A dimmer strip of the hero ribbon across the top of inner pages, fading into the black. */
export function RibbonBand() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 top-0 h-72 [mask-image:linear-gradient(to_bottom,black_30%,transparent)]"
    >
      <SoundRibbon intensity={0.55} />
    </div>
  );
}
