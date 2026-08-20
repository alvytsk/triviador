import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deadline, gameState, question } from "../../../../testing/factories";
import { renderWithApp } from "../../../../testing/render";
import { QuestionDock } from "./question-dock";

const CHOICE_TURN = {
  kind: "expansion_question" as const,
  question: question(),
  answered: [] as readonly string[],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 42,
  your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
};

const NUMERIC_TURN = {
  ...CHOICE_TURN,
  question: question({
    kind: "numeric" as const,
    choices: null,
    unit: "km",
  }),
};

describe("QuestionDock", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends submit_answer with kind: choice and the clicked idx", async () => {
    const harness = renderWithApp(<QuestionDock state={gameState({ turn: CHOICE_TURN })} />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "Labe" }));

    const frames = harness.socket.last().frames();
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({
      type: "submit_answer",
      deadline_id: 42,
      payload: { kind: "choice", idx: 1 },
    });
  });

  it("sends the numeric answer as the string it was typed, never a number", async () => {
    const harness = renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);
    act(() => harness.socket.last().open());

    await userEvent.type(screen.getByLabelText("YOUR ANSWER"), "0.1");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    const frames = harness.socket.last().frames();
    expect(frames).toHaveLength(1);
    const payload = frames[0]?.payload as Record<string, unknown>;
    expect(payload.value).toBe("0.1");
    expect(typeof payload.value).toBe("string");
  });

  it.each(["1e3", "1E-3", "-42", "0.1000000000000000000001", ".5", "5.", "1".repeat(40)])(
    "sends %s to the wire byte-identical — the server's grammar, not a stricter client one",
    async (typed) => {
      const harness = renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);
      act(() => harness.socket.last().open());

      await userEvent.type(screen.getByLabelText("YOUR ANSWER"), typed);
      await userEvent.click(screen.getByRole("button", { name: "Submit" }));

      const frames = harness.socket.last().frames();
      expect(frames).toHaveLength(1);
      const payload = frames[0]?.payload as Record<string, unknown>;
      expect(payload.value).toBe(typed);
    },
  );

  it.each(["NaN", "Infinity"])(
    "refuses %j with a visible reason and sends nothing",
    async (typed) => {
      const harness = renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);
      act(() => harness.socket.last().open());

      await userEvent.type(screen.getByLabelText("YOUR ANSWER"), typed);

      expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
      expect(screen.getByText(/enter a number the server can read/i)).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "Submit" }));
      expect(harness.socket.last().frames()).toHaveLength(0);
    },
  );

  it("opens on an untouched numeric field with no error shown and the unit visible, Submit still disabled", () => {
    renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);

    const input = screen.getByLabelText("YOUR ANSWER");
    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByText(/enter a number the server can read/i)).not.toBeInTheDocument();
    expect(screen.getByText("km")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
  });

  it("shows the error once the field has been touched and left invalid — even back at empty", async () => {
    renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);

    const input = screen.getByLabelText("YOUR ANSWER");
    await userEvent.type(input, "abc");
    await userEvent.clear(input);

    expect(screen.getByText(/enter a number the server can read/i)).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("caps typing at 40 characters and refuses a 41st with a visible reason", async () => {
    const harness = renderWithApp(<QuestionDock state={gameState({ turn: NUMERIC_TURN })} />);
    act(() => harness.socket.last().open());
    const input = screen.getByLabelText("YOUR ANSWER");

    // `maxLength` stops a real keystroke from ever landing a 41st
    // character — the same "refused keystroke the player can see" the
    // input aid is for. A pasted or scripted value can still bypass the
    // DOM's own keystroke-level enforcement, so the validity check behind
    // it is asserted directly too: a 41-character value set past that
    // enforcement must still refuse, visibly, exactly like any other
    // malformed input.
    await userEvent.type(input, "1".repeat(41));
    expect((input as HTMLInputElement).value).toHaveLength(40);

    fireEvent.change(input, { target: { value: "1".repeat(41) } });
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
    expect(screen.getByText(/enter a number the server can read/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(harness.socket.last().frames()).toHaveLength(0);
  });

  it("disables choices and sends nothing once the local deadline has passed", () => {
    vi.useFakeTimers();
    const harness = renderWithApp(
      <QuestionDock
        state={gameState({ turn: { ...CHOICE_TURN, deadline_at: deadline(1_000) } })}
      />,
    );
    act(() => harness.socket.last().open());
    act(() => vi.advanceTimersByTime(2_000));

    expect(screen.getByRole("button", { name: "Labe" })).toBeDisabled();
    expect(screen.getByText("Time is up.")).toBeInTheDocument();

    // `fireEvent`, not `userEvent`: under fake timers `userEvent`'s own
    // internal delay machinery needs real timers to resolve.
    act(() => fireEvent.click(screen.getByRole("button", { name: "Labe" })));
    expect(harness.socket.last().frames()).toHaveLength(0);
  });

  it("disables choices and says the answer was sent once your_answer is set", () => {
    renderWithApp(
      <QuestionDock
        state={gameState({
          turn: { ...CHOICE_TURN, your_answer: { kind: "choice", idx: 0, value: null } },
        })}
      />,
    );

    expect(screen.getByRole("button", { name: "Vltava" })).toBeDisabled();
    expect(screen.getByText("Answer sent.")).toBeInTheDocument();
  });

  it("renders the server's message for a not_your_turn rejection", async () => {
    const harness = renderWithApp(<QuestionDock state={gameState({ turn: CHOICE_TURN })} />);
    act(() => harness.socket.last().open());
    await userEvent.click(screen.getByRole("button", { name: "Labe" }));
    const commandId = harness.socket.last().frames()[0]?.command_id as string;

    act(() =>
      harness.socket.last().deliver({
        type: "error",
        command_id: commandId,
        code: "not_your_turn",
        message: "It is not your turn to answer.",
      }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent("It is not your turn to answer.");
  });

  it("carries no correct/incorrect marker on any choice before question_resolved", () => {
    renderWithApp(<QuestionDock state={gameState({ turn: CHOICE_TURN })} />);

    expect(screen.queryAllByTestId("choice-correct")).toHaveLength(0);
    expect(screen.queryAllByTestId("choice-incorrect")).toHaveLength(0);
  });

  // Default gameState() seats u1/u2/u3 and `you.player_id` is u1. Each of
  // these three turns excludes u1 from the participant list, so the
  // projection still sends the question but the server would reject u1's
  // answer with not_your_turn — the exact bug isYourTurn's fix exists for
  // (see the ledger's whole-branch review, Important #1).
  it("disables input for a seated bystander during another pair's battle_duel", () => {
    const bystanderDuel = {
      kind: "battle_duel" as const,
      attacker_id: "u2",
      defender_id: "u3",
      region_id: "praha",
      tiebreak: false,
      question: question(),
      answered: [] as readonly string[],
      your_answer: null,
      deadline_at: deadline(),
      deadline_id: 21,
      your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
    };
    renderWithApp(<QuestionDock state={gameState({ turn: bystanderDuel })} />);

    expect(screen.getByRole("button", { name: "Labe" })).toBeDisabled();
    expect(screen.getByText("Not your turn.")).toBeInTheDocument();
  });

  it("disables input for a seated bystander during another player's neutral_challenge", () => {
    const bystanderNeutral = {
      kind: "neutral_challenge" as const,
      attacker_id: "u2",
      region_id: "praha",
      question: question(),
      answered: [] as readonly string[],
      your_answer: null,
      deadline_at: deadline(),
      deadline_id: 22,
      your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
    };
    renderWithApp(<QuestionDock state={gameState({ turn: bystanderNeutral })} />);

    expect(screen.getByRole("button", { name: "Labe" })).toBeDisabled();
    expect(screen.getByText("Not your turn.")).toBeInTheDocument();
  });

  it("disables input for a seated bystander during a final_tiebreak between others", () => {
    const bystanderFinal = {
      kind: "final_tiebreak" as const,
      contenders: ["u2", "u3"],
      question: question(),
      answered: [] as readonly string[],
      your_answer: null,
      deadline_at: deadline(),
      deadline_id: 23,
      your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
    };
    renderWithApp(<QuestionDock state={gameState({ turn: bystanderFinal })} />);

    expect(screen.getByRole("button", { name: "Labe" })).toBeDisabled();
    expect(screen.getByText("Not your turn.")).toBeInTheDocument();
  });
});
