"use client";

import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

type DialogProps = {
  open: boolean;
  onClose: () => void;
  dialogId: string;
  ariaLabel: string;
  className?: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
};

type DialogLayer = {
  element: () => HTMLElement | null;
};

export function openDialogLayer<T>(layers: readonly T[], layer: T) {
  if (layers.includes(layer)) return { layers: [...layers], becameFirst: false };
  return { layers: [...layers, layer], becameFirst: layers.length === 0 };
}

export function closeDialogLayer<T>(layers: readonly T[], layer: T) {
  const index = layers.indexOf(layer);
  if (index < 0) {
    return { layers: [...layers], removed: false, wasTop: false, becameEmpty: false };
  }
  const next = [...layers.slice(0, index), ...layers.slice(index + 1)];
  return {
    layers: next,
    removed: true,
    wasTop: index === layers.length - 1,
    becameEmpty: next.length === 0,
  };
}

export function isTopDialogLayer<T>(layers: readonly T[], layer: T) {
  return layers.at(-1) === layer;
}

let activeDialogLayers: DialogLayer[] = [];
let backgroundShell: HTMLElement | null = null;
let backgroundShellWasInert = false;
let bodyOverflowBeforeDialogs = "";

function acquireDialogLayer(layer: DialogLayer) {
  const transition = openDialogLayer(activeDialogLayers, layer);
  activeDialogLayers = transition.layers;
  if (!transition.becameFirst) return;

  bodyOverflowBeforeDialogs = document.body.style.overflow;
  backgroundShell = document.querySelector<HTMLElement>(".app-shell");
  backgroundShellWasInert = backgroundShell?.hasAttribute("inert") ?? false;
  document.body.style.overflow = "hidden";
  backgroundShell?.setAttribute("inert", "");
}

function releaseDialogLayer(layer: DialogLayer) {
  const transition = closeDialogLayer(activeDialogLayers, layer);
  activeDialogLayers = transition.layers;
  if (transition.becameEmpty) {
    document.body.style.overflow = bodyOverflowBeforeDialogs;
    if (!backgroundShellWasInert) backgroundShell?.removeAttribute("inert");
    backgroundShell = null;
    backgroundShellWasInert = false;
    bodyOverflowBeforeDialogs = "";
  }
  return transition;
}

function visibleFocusableElements(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) => (
    !element.hidden
    && !element.closest("[hidden], [inert], [aria-hidden='true']")
    && element.getAttribute("aria-hidden") !== "true"
  ));
}

function firstFocusTarget(dialog: HTMLElement, preferred?: HTMLElement | null) {
  if (preferred && dialog.contains(preferred)) return preferred;
  return visibleFocusableElements(dialog)[0] ?? dialog;
}

export default function Dialog({
  open,
  onClose,
  dialogId,
  ariaLabel,
  className = "",
  initialFocusRef,
  returnFocusRef,
  children,
}: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  const initialFocusRefRef = useRef(initialFocusRef);
  const returnFocusRefRef = useRef(returnFocusRef);
  const layerRef = useRef<DialogLayer>({ element: () => dialogRef.current });
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    initialFocusRefRef.current = initialFocusRef;
  }, [initialFocusRef]);

  useEffect(() => {
    returnFocusRefRef.current = returnFocusRef;
  }, [returnFocusRef]);

  useEffect(() => {
    setPortalTarget(document.body);
  }, []);

  useEffect(() => {
    if (!open || !portalTarget) return;
    const layer = layerRef.current;
    const explicitReturnFocus = returnFocusRefRef.current?.current;
    const previousFocus = explicitReturnFocus?.isConnected
      ? explicitReturnFocus
      : document.activeElement instanceof HTMLElement ? document.activeElement : null;
    acquireDialogLayer(layer);

    const focusFrame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      if (!dialog || !isTopDialogLayer(activeDialogLayers, layer)) return;
      firstFocusTarget(dialog, initialFocusRefRef.current?.current).focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopDialogLayer(activeDialogLayers, layer)) return;
      const dialog = dialogRef.current;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const focusable = visibleFocusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
      const transition = releaseDialogLayer(layer);
      if (!transition.wasTop) return;
      const nextDialog = activeDialogLayers.at(-1)?.element() ?? null;
      const focusTarget = nextDialog
        ? (previousFocus && nextDialog.contains(previousFocus) ? previousFocus : firstFocusTarget(nextDialog))
        : previousFocus;
      window.requestAnimationFrame(() => {
        if (focusTarget?.isConnected) focusTarget.focus();
      });
    };
  }, [open, portalTarget]);

  if (!open || !portalTarget) return null;
  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && isTopDialogLayer(activeDialogLayers, layerRef.current)) {
          closeRef.current();
        }
      }}
    >
      <div
        ref={dialogRef}
        id={dialogId}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>,
    portalTarget,
  );
}
