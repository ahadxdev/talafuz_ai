import { useCallback, useReducer } from "react";

const HISTORY_LIMIT = 100;

function reducer(state, action) {
  switch (action.type) {
    case "update":
      return {
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: action.updater(state.present),
        future: [],
      };
    case "push":
      // Snapshot the current state once (used at the start of a live
      // interaction such as a slider drag or a typing session).
      if (state.past[state.past.length - 1] === state.present) return state;
      return {
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: state.present,
        future: [],
      };
    case "live":
      return { ...state, present: action.updater(state.present) };
    case "undo": {
      if (state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1];
      return {
        past: state.past.slice(0, -1),
        present: previous,
        future: [state.present, ...state.future].slice(0, HISTORY_LIMIT),
      };
    }
    case "redo": {
      if (state.future.length === 0) return state;
      const next = state.future[0];
      return {
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: next,
        future: state.future.slice(1),
      };
    }
    case "reset":
      return { past: [], present: action.state, future: [] };
    default:
      return state;
  }
}

/**
 * Undo/redo container for the editor document.
 *
 * `state` holds the whole editable document ({subtitles, language, style})
 * so undo/redo consistently covers text, timing, language and style changes.
 *
 * - update(updater)     — change state and record an undo entry
 * - pushHistory()       — snapshot current state (call once when a live
 *                         interaction begins, e.g. drag/typing)
 * - updateLive(updater) — change state without recording (during the drag)
 * - undo()/redo()       — time travel
 */
export function useEditorHistory(initialState) {
  const [history, dispatch] = useReducer(reducer, {
    past: [],
    present: initialState,
    future: [],
  });

  const update = useCallback(
    (updater) => dispatch({ type: "update", updater }),
    []
  );
  const pushHistory = useCallback(() => dispatch({ type: "push" }), []);
  const updateLive = useCallback(
    (updater) => dispatch({ type: "live", updater }),
    []
  );
  const undo = useCallback(() => dispatch({ type: "undo" }), []);
  const redo = useCallback(() => dispatch({ type: "redo" }), []);
  const reset = useCallback((state) => dispatch({ type: "reset", state }), []);

  return {
    state: history.present,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    update,
    pushHistory,
    updateLive,
    undo,
    redo,
    reset,
  };
}
