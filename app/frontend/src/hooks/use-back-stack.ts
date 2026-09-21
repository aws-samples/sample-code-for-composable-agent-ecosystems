"use client";
// use-back-stack — make the browser Back button unwind IN-VIEW drill-downs (selecting a
// catalog domain, opening a record, opening a domain's detail screen, opening a run) before
// it changes the top-level page.
//
// The app is a state-driven SPA: top-level navigation is the `view` state var (mirrored to
// the URL hash in page.tsx). But drill-downs INSIDE a view are component-local state that
// the browser history knows nothing about, so Back used to jump straight out of the whole
// view. This primitive lets any drill-down register a `back` handler that reverts its local
// state; Back then pops one drill-down level at a time, and only leaves the view once the
// stack is empty.
//
// How it works:
//   • pushStep(back) pushes a browser history entry tagged {__backStep: id} AND records the
//     handler on an in-memory stack.
//   • The single popstate listener here fires FIRST (capture phase). If the entry being left
//     was one of our steps (i.e. the top of our stack), it runs that handler (reverting the
//     drill-down) and swallows the event so page.tsx's view-level popstate handler does NOT
//     also change the view.
//   • If a component reverts its own state via its in-app "← Back" affordance, it calls
//     popStep() which history.back()s to keep the browser stack in sync.
//
// This is intentionally decoupled: components only supply a `back` closure — they don't need
// to serialise their state into the URL.
import { createContext, useContext } from "react";

export interface BackStep {
  id: number;
  /** Revert the drill-down this step represents (e.g. setSelected(null)). */
  back: () => void;
}

export interface BackStackApi {
  /** Register a drill-down step. Returns a disposer that removes it (and, if it is still the
   *  current top, steps the browser history back to stay in sync). Call the disposer when the
   *  component reverts the drill-down itself (in-app back link) or unmounts. */
  pushStep: (back: () => void) => () => void;
}

// A no-op default so components work even if the provider is absent (e.g. isolated tests).
export const BackStackContext = createContext<BackStackApi>({
  pushStep: () => () => {},
});

export function useBackStack(): BackStackApi {
  return useContext(BackStackContext);
}
