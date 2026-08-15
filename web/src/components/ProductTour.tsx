import { useEffect, useRef, useState } from "react";
import "../product-tour.css";

type TourStep = {
  eyebrow: string;
  title: string;
  body: string;
  target?: string;
};

const STEPS: TourStep[] = [
  {
    eyebrow: "60-SECOND PRODUCT TOUR",
    title: "One system, five focused workspaces.",
    body: "A field consultant captures evidence. AI structures it. An independent reviewer decides. Operators close the loop, leaders see patterns, and technical evaluators inspect the machinery.",
  },
  {
    eyebrow: "1 · CAPTURE NATURALLY",
    title: "Talk, photograph, film or type—then keep walking.",
    body: "The composer saves evidence first and analyzes in the background. Voice transcripts must be confirmed by the consultant before they can become a candidate finding.",
    target: '[data-tour="capture"], [data-tour="start-visit"]',
  },
  {
    eyebrow: "2 · COVER WHAT MATTERS",
    title: "The guide changes with the current area.",
    body: "Each prompt shows whether it comes from law, a conditional rule, industry best practice or venue policy. Pass, flag an issue, or mark it not applicable—and attach only evidence that actually supports it.",
    target: '[data-tour="area-guide"]',
  },
  {
    eyebrow: "3 · HUMAN DECISION",
    title: "AI prepares; an independent reviewer approves.",
    body: "The review packet keeps evidence, the cited source, interpretation, uncertainty and unsupported claims separate. Reviewers can correct, request evidence, reject or approve with a reasoned trail.",
  },
  {
    eyebrow: "4 · CLOSE THE LOOP",
    title: "Operators receive actions, not another dashboard.",
    body: "Validate the condition, assign the owner, capture before-and-after proof, verify the correction and prepare a customer reply without treating public reviews as compliance evidence.",
  },
  {
    eyebrow: "5 · LEARN ACROSS VISITS",
    title: "Leaders see recurrence and customer impact.",
    body: "Portfolio and customer-context views surface repeated issues, verified closures and competitor themes while preserving provenance and sample limits.",
  },
  {
    eyebrow: "6 · INSPECT THE AI",
    title: "The technical view makes the system falsifiable.",
    body: "Provider health, model-call cost, traces, source outcomes and evaluation gates are visible. Fixture fallbacks and degraded runs are labelled instead of being presented as live success.",
  },
  {
    eyebrow: "YOU'RE READY",
    title: "Start with the field workflow.",
    body: "Choose an area, capture what you genuinely observe, complete the small sourced guide, then hand the packet to the reviewer. You can replay this tour from Help at any time.",
    target: '[data-tour="capture"], [data-tour="start-visit"]',
  },
];

export default function ProductTour({ open, onClose }: {
  open: boolean;
  onClose: (completed: boolean) => void;
}) {
  const [index, setIndex] = useState(0);
  const [locallyDismissed, setLocallyDismissed] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const step = STEPS[index];

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => {
      document.querySelectorAll(".product-tour-target").forEach(node =>
        node.classList.remove("product-tour-target"));
      const target = step.target ? document.querySelector(step.target) : null;
      target?.classList.add("product-tour-target");
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
      panel.current?.focus();
    }, 120);
    return () => {
      window.clearTimeout(timer);
      document.querySelectorAll(".product-tour-target").forEach(node =>
        node.classList.remove("product-tour-target"));
    };
  }, [index, open]);

  useEffect(() => {
    if (!open) return;
    const background = document.querySelectorAll<HTMLElement>(
      ".side, .main, .mobile-nav, .mobile-tour-launch",
    );
    background.forEach(node => {
      node.inert = true;
      node.setAttribute("aria-hidden", "true");
    });
    return () => background.forEach(node => {
      node.inert = false;
      node.removeAttribute("aria-hidden");
    });
  }, [open]);

  useEffect(() => {
    if (!open) {
      setIndex(0);
      setLocallyDismissed(false);
    }
  }, [open]);

  const close = (completed: boolean) => {
    setLocallyDismissed(true);
    onClose(completed);
  };

  if (!open || locallyDismissed) return null;
  const last = index === STEPS.length - 1;

  return <div className="product-tour" role="presentation">
    <div className="product-tour-shade" onClick={() => close(false)} />
    <div className="product-tour-panel" role="dialog" aria-modal="true"
      aria-label="Product walkthrough" tabIndex={-1} ref={panel}
      onKeyDown={event => {
        if (event.key === "Escape") close(false);
        if (event.key === "ArrowRight" && !last) setIndex(value => value + 1);
        if (event.key === "ArrowLeft" && index > 0) setIndex(value => value - 1);
        if (event.key === "Tab") {
          const buttons = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
          if (!buttons.length) return;
          const first = buttons[0];
          const final = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); final.focus();
          } else if (!event.shiftKey && document.activeElement === final) {
            event.preventDefault(); first.focus();
          }
        }
      }}>
      <div className="product-tour-progress" aria-label={`Tour step ${index + 1} of ${STEPS.length}`}>
        {STEPS.map((_, stepIndex) => <span key={stepIndex} className={stepIndex <= index ? "done" : ""} />)}
      </div>
      <span className="product-tour-eyebrow">{step.eyebrow}</span>
      <h2>{step.title}</h2>
      <p>{step.body}</p>
      <div className="product-tour-actions">
        <button type="button" className="product-tour-skip"
          onClick={event => { event.preventDefault(); event.stopPropagation(); close(false); }}>Skip tour</button>
        <div>
          {index > 0 && <button onClick={() => setIndex(value => value - 1)}>Back</button>}
          <button className="product-tour-next" onClick={() => last ? close(true) : setIndex(value => value + 1)}>
            {last ? "Start walkthrough" : "Next"}
          </button>
        </div>
      </div>
      <small>{index + 1} of {STEPS.length} · Arrow keys also work</small>
    </div>
  </div>;
}
