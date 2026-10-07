import type { Metadata } from "next";
import { RibbonBand } from "@/components/ribbon-band";
import { Suspense } from "react";
import { AskApp } from "./ask-app";

export const metadata: Metadata = { title: "Ask" };

export default function AskPage() {
  return (
    <>
      <RibbonBand />
      <main className="relative mx-auto grid max-w-3xl gap-6 px-4 pt-12 pb-20 sm:px-6">
        <div className="grid gap-2">
          <h1 className="font-display text-4xl font-bold">Ask the podcasts</h1>
          <p className="text-ink-2">
            Answers come only from the indexed episodes, and every quote is checked against the transcript.
          </p>
        </div>
        {/* useSearchParams (for ?q=) needs a Suspense boundary so the rest of the page stays static. */}
        <Suspense fallback={<div className="h-24 rounded-md border border-edge bg-panel" />}>
          <AskApp />
        </Suspense>
      </main>
    </>
  );
}
