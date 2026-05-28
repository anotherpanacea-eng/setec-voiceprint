#!/usr/bin/env python3
"""narrative_feature_schema.py — the 30 core narrative-decision features.

Source of truth: Russell et al., "StoryScope: Narrative-Level Detection
of AI-Generated Fiction" (arXiv 2604.03136v4, COLM 2026 submission),
Table 12 (`tab:core_features_themed`). The paper induces these features
over a parallel 10,272-prompt × 6-source corpus of ~5,000-word short
stories, validates them with Cohen's κ = 0.84 against human annotators,
and reports them as the compact subset that retains ~91% of the
narrative-only binary detector's macro-F1.

Important framing notes — load-bearing for downstream consumers:

1. **These are narrative-decision features, not stylistic features.**
   The paper's headline contrast is with AIC-style tells (em-dashes,
   "delve", surprisal, etc.). Removing these features requires
   structural rewrites of the story, not phrase-level substitution.
   The paper reports detection drops only 1.6 macro-F1 points after
   LAMP-style stylistic rewriting (95.5 → 93.9). For SETEC purposes,
   that means this surface is *complementary* to AIC-7/8/9 and
   Tier-1 variance signals, not a replacement.

2. **The paper's home register is long-form fiction (mean 4,753
   words, NarraBench taxonomy is literary).** Several features
   (subplot integration, anachrony intensity, frame narratives) are
   not computable on essays/op-eds/emails and degrade silently when
   asked on out-of-register prose. The audit script gates on word
   count and emits a register-mismatch warning when the target looks
   non-fiction. The cross-corpus polarity check (EditLens essays
   vs. fiction baseline) is the validation step parallel to
   `calibration-findings-2026-05-10.md` and -11-mage.md.

3. **Each feature's `human_mean` and `ai_mean` are the paper's
   reported group means.** They are *literature anchors*, not
   per-corpus thresholds. Operators running this surface on their
   own corpora MUST treat the means as orientation hints, not as
   adjudicative cut-points. The literature-anchored scorer
   (`narrative_decision_audit.compute_scorer`) emits a signed
   numeric score that aggregates the per-feature deviations, but
   the verdict band remains `uncalibrated` unless an operator
   supplies their own cross-corpus thresholds via the calibration
   script.

4. **Three features have human-leaning AND AI-leaning option values.**
   Categorical features like "Subplot Integration", "Reference
   Explicitness", and "Dominant Emotional Expression" appear in
   both the AI-elevated and human-elevated halves of the paper's
   Table 12 because each option value carries an independent signal
   (e.g., `no_subplots` is AI-elevated; `thematically_parallel` is
   human-elevated; other options are neutral). The schema encodes
   these as one underlying feature with a `signals` list mapping
   per-option leanings.

   This yields 30 distinct underlying features producing 33 signal
   entries in Table 12 (= 27 single-leaning features + 3
   dual-leaning features × 2 signals each).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "FeatureSignal",
    "CoreFeature",
    "CORE_FEATURES",
    "BUNDLE_LABELS",
    "DIMENSION_LABELS",
    "TYPE_LABELS",
    "iter_signals",
]


FeatureType = Literal["scale", "ordinal", "categorical", "binary", "multi"]
Leaning = Literal["ai", "human"]

# NarraBench dimension prefixes used in the paper.
DIMENSION_LABELS: dict[str, str] = {
    "SIT": "Situatedness",
    "AGENT": "Agents",
    "PLT": "Plot",
    "EVT": "Events",
    "SET": "Setting",
    "REV": "Revelation",
    "TMP": "Time",
    "PER": "Perspective",
    "SOC": "Social Network",
    "STR": "Structure",
}

# Bundle labels (the seven interpretive themes in Table 12).
BUNDLE_LABELS: dict[str, str] = {
    "thematic_over_determination": "AI-elevated: Thematic over-determination",
    "sensory_embodied_performativity": "AI-elevated: Sensory & embodied performativity",
    "structural_streamlining": "AI-elevated: Structural streamlining",
    "intertextual_richness": "Human-elevated: Intertextual richness",
    "reader_engagement": "Human-elevated: Reader engagement",
    "temporal_complexity": "Human-elevated: Temporal complexity",
    "narrative_diversity": "Human-elevated: Narrative diversity",
}

TYPE_LABELS: dict[str, str] = {
    "scale": "1–5 Likert",
    "ordinal": "ordinal",
    "categorical": "categorical (single-select)",
    "binary": "binary (yes/no)",
    "multi": "categorical (multi-select)",
}


@dataclass(frozen=True)
class FeatureSignal:
    """One row in the paper's Table 12.

    A single underlying feature can produce multiple signals when more
    than one option value carries independent direction. For
    scale/ordinal/binary features there is exactly one signal whose
    ``option`` is None and whose comparison is over the numeric value.
    For categorical/multi features the ``option`` names the response
    value whose *prevalence* is the comparison, and ``human_mean`` /
    ``ai_mean`` are reported as probabilities in [0, 1] (the paper
    reports percentages; we store the fraction).
    """

    option: str | None
    leaning: Leaning
    human_mean: float
    ai_mean: float
    bundle: str

    @property
    def gap(self) -> float:
        """Human mean minus AI mean (sign matches paper Table 12).

        Negative gap = AI-elevated; positive gap = human-elevated.
        For categorical/multi signals, this is the prevalence
        difference in proportion units (e.g., 0.42 = 42 percentage
        points). For scale/ordinal/binary signals, it is the
        difference in encoded-value means.
        """
        return self.human_mean - self.ai_mean


@dataclass(frozen=True)
class CoreFeature:
    """One of the 30 core features.

    ``key`` is the stable identifier used in JSON output and in the
    signals glossary path (`narrative.<bundle>.<key>`). ``dimension``
    is the NarraBench dimension prefix. ``question`` is the prompt
    text passed to the LLM judge. ``response_options`` enumerates
    the legal response values; for scale/ordinal types it is the
    integer range encoded as strings.
    """

    key: str
    label: str
    dimension: str
    feature_type: FeatureType
    question: str
    description: str
    response_options: tuple[str, ...]
    signals: tuple[FeatureSignal, ...]
    paper_table_row: int

    @property
    def is_dual_leaning(self) -> bool:
        return len({s.leaning for s in self.signals}) > 1


# ---------- the 30 core features ---------------------------------

# Encoded verbatim from Russell et al. 2026 Tables 10, 11, and 12.
# Means/proportions transcribed to two decimal places where the
# paper reports them; categorical options are stored in their
# paper-canonical lowercase-snake form. Where Table 12 quotes a
# percentage (e.g. "77%"), we store it as a probability (0.77).

CORE_FEATURES: tuple[CoreFeature, ...] = (
    # ------ Thematic over-determination (AI-elevated) -------------
    CoreFeature(
        key="thematic_explicitness_and_moralizing",
        label="Thematic Explicitness and Moralizing",
        dimension="SIT",
        feature_type="scale",
        question=(
            "How explicitly does the story articulate its themes or "
            "morals? Score 1 (themes purely implicit) to 5 (themes "
            "stated outright by the narrator or a character, with "
            "explicit moral takeaway)."
        ),
        description=(
            "Rate the degree to which the story spells out what it "
            "is 'about' rather than leaving thematic content for "
            "the reader to infer."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.28, ai_mean=3.94,
                bundle="thematic_over_determination",
            ),
        ),
        paper_table_row=1,
    ),
    CoreFeature(
        key="moral_philosophical_weighting",
        label="Moral / Philosophical Weighting",
        dimension="SIT",
        feature_type="scale",
        question=(
            "How heavily does the story foreground moral or "
            "philosophical questions? Score 1 (no moral/philosophical "
            "weighting) to 5 (foregrounded as central concern)."
        ),
        description=(
            "Rate the prominence of moral or philosophical questions "
            "as load-bearing concerns of the narrative."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.26, ai_mean=3.68,
                bundle="thematic_over_determination",
            ),
        ),
        paper_table_row=15,
    ),
    CoreFeature(
        key="thematic_unity",
        label="Thematic Unity",
        dimension="PLT",
        feature_type="scale",
        question=(
            "To what extent do subplots and flourishes serve a "
            "central thematic concern? Score 1 (subplots feel "
            "independent of any unifying theme) to 5 (every element "
            "echoes one central thematic concern)."
        ),
        description=(
            "Rate how tightly the story's components converge on a "
            "single thematic spine."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=4.41, ai_mean=4.74,
                bundle="thematic_over_determination",
            ),
        ),
        paper_table_row=3,
    ),
    CoreFeature(
        key="narratorial_thematic_commentary",
        label="Narratorial Thematic Commentary",
        dimension="SIT",
        feature_type="binary",
        question=(
            "Does the narrator explicitly comment on the story's "
            "themes from outside any character's perspective? Answer "
            "'yes' if the narrative voice steps back to gloss what "
            "the story means; 'no' if all thematic content is filtered "
            "through characters."
        ),
        description=(
            "Detect explicit narrator-level thematic commentary "
            "(e.g., 'Some lessons can only be learned at cost.')."
        ),
        response_options=("no", "yes"),
        signals=(
            FeatureSignal(
                option="yes", leaning="ai",
                human_mean=0.52, ai_mean=0.77,
                bundle="thematic_over_determination",
            ),
        ),
        paper_table_row=10,
    ),
    CoreFeature(
        key="dialogue_function",
        label="Dialogue Function",
        dimension="PER",
        feature_type="multi",
        question=(
            "Which of the following functions does the dialogue "
            "primarily serve? Select all that apply: 'advance_plot' "
            "(moves events forward), 'reveal_character' (shows "
            "personality/history), 'worldbuilding' (delivers "
            "setting/context), 'philosophical_debate' (characters "
            "argue ideas), 'comic_relief' (humor)."
        ),
        description=(
            "Tag the primary purposes of in-story dialogue. The "
            "AI-elevated signal is on 'philosophical_debate'."
        ),
        response_options=(
            "advance_plot",
            "reveal_character",
            "worldbuilding",
            "philosophical_debate",
            "comic_relief",
        ),
        signals=(
            FeatureSignal(
                option="philosophical_debate", leaning="ai",
                human_mean=0.34, ai_mean=0.59,
                bundle="thematic_over_determination",
            ),
        ),
        paper_table_row=12,
    ),
    CoreFeature(
        key="reference_explicitness",
        label="Reference Explicitness",
        dimension="SIT",
        feature_type="categorical",
        question=(
            "How explicit are the story's intertextual gestures? "
            "Choose one: 'none' (no detectable references), "
            "'explicit_named' (specific named works/authors/places), "
            "'implicit_echoes' (allusive but unspecified), "
            "'balanced_mix' (both explicit and implicit)."
        ),
        description=(
            "Classify the dominant mode of intertextual reference. "
            "AI elevates 'implicit_echoes'; humans elevate "
            "'balanced_mix'."
        ),
        response_options=(
            "none",
            "explicit_named",
            "implicit_echoes",
            "balanced_mix",
        ),
        signals=(
            FeatureSignal(
                option="implicit_echoes", leaning="ai",
                human_mean=0.50, ai_mean=0.72,
                bundle="thematic_over_determination",
            ),
            FeatureSignal(
                option="balanced_mix", leaning="human",
                human_mean=0.37, ai_mean=0.16,
                bundle="intertextual_richness",
            ),
        ),
        paper_table_row=16,
    ),
    # ------ Sensory & embodied performativity (AI-elevated) ------
    CoreFeature(
        key="dominant_emotional_expression",
        label="Dominant Emotional Expression",
        dimension="AGENT",
        feature_type="categorical",
        question=(
            "How are characters' emotions most commonly conveyed? "
            "Choose one: 'explicit_labels' (named emotions: 'she "
            "felt afraid'), 'embodied_metaphors' (bodies/sensations: "
            "'her chest tightened'), 'behavioral_cues' (actions "
            "without naming: she paced), 'ambiguous' (emotion is "
            "obscured)."
        ),
        description=(
            "Classify the dominant rhetorical mode for showing "
            "emotion. AI elevates 'embodied_metaphors'; humans "
            "elevate 'explicit_labels'."
        ),
        response_options=(
            "explicit_labels",
            "embodied_metaphors",
            "behavioral_cues",
            "ambiguous",
        ),
        signals=(
            FeatureSignal(
                option="embodied_metaphors", leaning="ai",
                human_mean=0.38, ai_mean=0.81,
                bundle="sensory_embodied_performativity",
            ),
            FeatureSignal(
                option="explicit_labels", leaning="human",
                human_mean=0.29, ai_mean=0.08,
                bundle="narrative_diversity",
            ),
        ),
        paper_table_row=2,
    ),
    CoreFeature(
        key="setting_as_psychological_mirror",
        label="Setting as Psychological Mirror",
        dimension="SET",
        feature_type="scale",
        question=(
            "To what degree does the physical environment mirror "
            "characters' inner states? Score 1 (setting is neutral "
            "backdrop) to 5 (setting is consistently weather-"
            "of-the-soul; objective correlative throughout)."
        ),
        description=(
            "Rate the strength of pathetic-fallacy / objective-"
            "correlative use of setting."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.58, ai_mean=4.07,
                bundle="sensory_embodied_performativity",
            ),
        ),
        paper_table_row=6,
    ),
    CoreFeature(
        key="environmental_ecological_emphasis",
        label="Environmental and Ecological Emphasis",
        dimension="SET",
        feature_type="scale",
        question=(
            "How prominent is the natural environment or ecology in "
            "the narrative? Score 1 (environment is incidental) to 5 "
            "(natural/ecological setting is a load-bearing element)."
        ),
        description=(
            "Rate the foregrounding of natural environment, weather, "
            "and ecological detail."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=2.83, ai_mean=3.21,
                bundle="sensory_embodied_performativity",
            ),
        ),
        paper_table_row=17,
    ),
    CoreFeature(
        key="dominant_sensory_modalities",
        label="Dominant Sensory Modalities",
        dimension="SET",
        feature_type="multi",
        question=(
            "Which sensory modalities does the story most frequently "
            "engage? Select all that are prominent: 'visual', "
            "'auditory', 'olfactory', 'tactile', 'gustatory', "
            "'kinesthetic'."
        ),
        description=(
            "Tag the sensory channels the prose recruits. The "
            "AI-elevated signal is on 'olfactory'."
        ),
        response_options=(
            "visual",
            "auditory",
            "olfactory",
            "tactile",
            "gustatory",
            "kinesthetic",
        ),
        signals=(
            FeatureSignal(
                option="olfactory", leaning="ai",
                human_mean=0.57, ai_mean=0.82,
                bundle="sensory_embodied_performativity",
            ),
        ),
        paper_table_row=4,
    ),
    CoreFeature(
        key="sensory_density",
        label="Sensory Density",
        dimension="SET",
        feature_type="scale",
        question=(
            "How dense is sensory description across the narrative? "
            "Score 1 (minimal sensory detail) to 5 (lush, "
            "continuous sensory rendering)."
        ),
        description=(
            "Rate the per-paragraph rate of concrete sensory "
            "description."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.66, ai_mean=3.93,
                bundle="sensory_embodied_performativity",
            ),
        ),
        paper_table_row=8,
    ),
    CoreFeature(
        key="depth_of_interior_access",
        label="Depth of Interior Access",
        dimension="PER",
        feature_type="scale",
        question=(
            "How deep into characters' interior life does the "
            "narration go? Score 1 (purely external observation) to "
            "5 (sustained, granular access to inner thought, "
            "memory, sensation)."
        ),
        description=(
            "Rate the depth and continuity of the narrator's access "
            "to characters' inner mental states."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.67, ai_mean=3.93,
                bundle="sensory_embodied_performativity",
            ),
        ),
        paper_table_row=20,
    ),
    # ------ Structural streamlining (AI-elevated) -----------------
    CoreFeature(
        key="continuity_of_main_causal_chain",
        label="Continuity of Main Causal Chain",
        dimension="EVT",
        feature_type="scale",
        question=(
            "How continuous is the single causal chain from inciting "
            "incident to ending? Score 1 (causal links are loose or "
            "broken) to 5 (every event tightly causes the next, no "
            "loose ends)."
        ),
        description=(
            "Rate the tightness of cause-and-effect linkage along "
            "the primary plot line."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=3.92, ai_mean=4.20,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=7,
    ),
    CoreFeature(
        key="spatial_granularity_level",
        label="Spatial Granularity Level",
        dimension="SET",
        feature_type="ordinal",
        question=(
            "How fine-grained is the depiction of physical space? "
            "Choose one: 'very_low' (no spatial detail), 'low', "
            "'medium', 'high' (rooms, objects, distances are "
            "rendered precisely)."
        ),
        description=(
            "Rate the resolution at which physical space is drawn."
        ),
        response_options=("very_low", "low", "medium", "high"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=2.27, ai_mean=2.53,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=13,
    ),
    CoreFeature(
        key="agency_in_resolution",
        label="Agency in Resolution",
        dimension="PLT",
        feature_type="categorical",
        question=(
            "Is the story's resolution driven by the protagonist's "
            "choices or by external events? Choose one: "
            "'protagonist_choice' (resolution flows from the "
            "protagonist's decisions), 'mixed' (both), "
            "'external_fate' (resolution imposed by the world)."
        ),
        description=(
            "Identify the locus of agency at the resolution. AI "
            "elevates 'protagonist_choice'."
        ),
        response_options=(
            "protagonist_choice",
            "mixed",
            "external_fate",
        ),
        signals=(
            FeatureSignal(
                option="protagonist_choice", leaning="ai",
                human_mean=0.46, ai_mean=0.69,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=9,
    ),
    CoreFeature(
        key="character_introduction",
        label="Character Introduction",
        dimension="AGENT",
        feature_type="categorical",
        question=(
            "What narrative device primarily introduces the central "
            "character? Choose one: 'external_description' (narrator "
            "describes appearance/role), 'in_action' (we meet them "
            "doing), 'in_dialogue' (introduced via speech), "
            "'inner_thought' (introduced via their own interior), "
            "'others_reports' (introduced through other characters' "
            "talk)."
        ),
        description=(
            "Classify the dominant mode by which the main character "
            "enters the story. AI elevates 'external_description'."
        ),
        response_options=(
            "external_description",
            "in_action",
            "in_dialogue",
            "inner_thought",
            "others_reports",
        ),
        signals=(
            FeatureSignal(
                option="external_description", leaning="ai",
                human_mean=0.30, ai_mean=0.52,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=5,
    ),
    CoreFeature(
        key="subplot_integration",
        label="Subplot Integration",
        dimension="PLT",
        feature_type="categorical",
        question=(
            "How do subplots relate to the central plot/theme? "
            "Choose one: 'no_subplots' (single-track story), "
            "'thematically_parallel' (subplots echo central theme), "
            "'contrasting' (subplots cut against the central "
            "theme), 'independent' (subplots stand alone)."
        ),
        description=(
            "Classify subplot architecture. AI elevates "
            "'no_subplots'; humans elevate 'thematically_parallel'."
        ),
        response_options=(
            "no_subplots",
            "thematically_parallel",
            "contrasting",
            "independent",
        ),
        signals=(
            FeatureSignal(
                option="no_subplots", leaning="ai",
                human_mean=0.57, ai_mean=0.79,
                bundle="structural_streamlining",
            ),
            FeatureSignal(
                option="thematically_parallel", leaning="human",
                human_mean=0.42, ai_mean=0.21,
                bundle="narrative_diversity",
            ),
        ),
        paper_table_row=14,
    ),
    CoreFeature(
        key="mode_of_resolution",
        label="Mode of Resolution",
        dimension="EVT",
        feature_type="categorical",
        question=(
            "Is the main event chain resolved through external "
            "action or internal acceptance, or is it left "
            "unresolved? Choose one: 'resolved_externally' "
            "(external event closes the chain), "
            "'resolved_internally' (a character's understanding or "
            "acceptance closes it), 'unresolved' (chain left open)."
        ),
        description=(
            "Classify how the main event chain terminates. AI "
            "elevates 'resolved_internally'."
        ),
        response_options=(
            "resolved_externally",
            "resolved_internally",
            "unresolved",
        ),
        signals=(
            FeatureSignal(
                option="resolved_internally", leaning="ai",
                human_mean=0.27, ai_mean=0.47,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=18,
    ),
    CoreFeature(
        key="opening_spatial_grounding",
        label="Opening Spatial Grounding",
        dimension="SET",
        feature_type="ordinal",
        question=(
            "How clearly does the opening of the story ground the "
            "reader in a specific physical setting? Choose one: "
            "'none_or_vague' (no clear setting), 'minimal' (some "
            "spatial cues), 'clear_local' (room/scene fixed), "
            "'clear_local_and_global' (local setting and broader "
            "geography both fixed)."
        ),
        description=(
            "Rate spatial concreteness within the opening scene."
        ),
        response_options=(
            "none_or_vague",
            "minimal",
            "clear_local",
            "clear_local_and_global",
        ),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=2.12, ai_mean=2.33,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=11,
    ),
    CoreFeature(
        key="pre_threat_character_investment",
        label="Pre-Threat Character Investment",
        dimension="REV",
        feature_type="scale",
        question=(
            "How much does the story build emotional/dramatic "
            "investment in the protagonist before major jeopardy "
            "arrives? Score 1 (jeopardy lands immediately) to 5 "
            "(extended runway of investment before any threat)."
        ),
        description=(
            "Rate the runway given to character investment prior to "
            "the inciting jeopardy."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="ai",
                human_mean=2.76, ai_mean=2.99,
                bundle="structural_streamlining",
            ),
        ),
        paper_table_row=19,
    ),
    # ------ Intertextual richness (Human-elevated) ----------------
    CoreFeature(
        key="intertextual_strategy_types",
        label="Intertextual Strategy Types",
        dimension="SIT",
        feature_type="multi",
        question=(
            "Which kinds of intertextual engagement does the story "
            "employ? Select all that apply: 'explicit_named' "
            "(specific named work/author/place), 'retelling' "
            "(reworks a known story), 'pastiche' (imitates a known "
            "style), 'myth_or_religion' (engages mythic/religious "
            "tradition), 'self_referential' (refers to its own "
            "status as a story)."
        ),
        description=(
            "Tag the modes of intertextual engagement. The "
            "human-elevated signal is on 'explicit_named'."
        ),
        response_options=(
            "explicit_named",
            "retelling",
            "pastiche",
            "myth_or_religion",
            "self_referential",
        ),
        signals=(
            FeatureSignal(
                option="explicit_named", leaning="human",
                human_mean=0.47, ai_mean=0.24,
                bundle="intertextual_richness",
            ),
        ),
        paper_table_row=21,
    ),
    # (reference_explicitness/balanced_mix already encoded above as
    # the second signal on reference_explicitness; no separate row.)
    # ------ Reader engagement (Human-elevated) --------------------
    CoreFeature(
        key="fourth_wall_permeability",
        label="Fourth-Wall Permeability",
        dimension="SIT",
        feature_type="ordinal",
        question=(
            "To what extent does the story break the boundary "
            "between story-world and reader? Choose one: 'none' "
            "(no breaks), 'occasional_asides' (mild gestures to "
            "the reader), 'frequent_or_structural' (sustained "
            "address), 'radical_violations' (the reader is named, "
            "metafictional collapse)."
        ),
        description=(
            "Rate the porosity of the fourth wall."
        ),
        response_options=(
            "none",
            "occasional_asides",
            "frequent_or_structural",
            "radical_violations",
        ),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=0.67, ai_mean=0.39,
                bundle="reader_engagement",
            ),
        ),
        paper_table_row=23,
    ),
    CoreFeature(
        key="frequency_of_direct_reader_address",
        label="Frequency of Direct Reader Address",
        dimension="PER",
        feature_type="ordinal",
        question=(
            "How often does the text directly address the reader "
            "('dear reader', 'you, who are reading this', etc.)? "
            "Choose one: 'never', 'occasional_asides', "
            "'frequent_or_structural'."
        ),
        description=(
            "Rate the rate of explicit reader address (excluding "
            "in-world second-person dialogue)."
        ),
        response_options=(
            "never",
            "occasional_asides",
            "frequent_or_structural",
        ),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=0.28, ai_mean=0.07,
                bundle="reader_engagement",
            ),
        ),
        paper_table_row=22,
    ),
    # ------ Temporal complexity (Human-elevated) -----------------
    CoreFeature(
        key="depth_of_recontextualization_after_surprise",
        label="Depth of Recontextualization After Surprise",
        dimension="REV",
        feature_type="scale",
        question=(
            "After a major revelation, how extensively must the "
            "reader reinterpret earlier scenes? Score 1 (no "
            "recontextualization needed) to 5 (complete re-reading "
            "of prior scenes is required)."
        ),
        description=(
            "Rate the retroactive reinterpretation a story's "
            "revelations impose on prior scenes."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=3.28, ai_mean=2.95,
                bundle="temporal_complexity",
            ),
        ),
        paper_table_row=24,
    ),
    CoreFeature(
        key="degree_of_chronological_discontinuity",
        label="Degree of Chronological Discontinuity",
        dimension="TMP",
        feature_type="scale",
        question=(
            "How often does the narrative jump across time? Score 1 "
            "(strictly linear) to 5 (constant time jumps; reader "
            "must reconstruct chronology)."
        ),
        description=(
            "Rate the rate of explicit temporal jumps (forward or "
            "backward) in the narrative."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=2.40, ai_mean=2.12,
                bundle="temporal_complexity",
            ),
        ),
        paper_table_row=25,
    ),
    CoreFeature(
        key="nonlinear_framing_for_delayed_disclosure",
        label="Nonlinear Framing for Delayed Disclosure",
        dimension="REV",
        feature_type="scale",
        question=(
            "To what extent does the story use time jumps to stage "
            "revelations? Score 1 (linear; reveals follow "
            "discovery) to 5 (heavily fragmented chronology used "
            "structurally to delay or stage disclosure)."
        ),
        description=(
            "Rate the structural use of nonlinear time as a "
            "revelation device."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=1.96, ai_mean=1.68,
                bundle="temporal_complexity",
            ),
        ),
        paper_table_row=29,
    ),
    CoreFeature(
        key="anachrony_intensity",
        label="Anachrony Intensity",
        dimension="TMP",
        feature_type="scale",
        question=(
            "How heavily does the narrative rely on flashbacks or "
            "flash-forwards? Score 1 (absent) to 5 (dominant "
            "anachronic structure)."
        ),
        description=(
            "Rate the load-bearing weight of anachrony (flashbacks, "
            "flash-forwards) in the narrative."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=2.58, ai_mean=2.31,
                bundle="temporal_complexity",
            ),
        ),
        paper_table_row=27,
    ),
    # ------ Narrative diversity (Human-elevated) -----------------
    CoreFeature(
        key="location_variety_scope",
        label="Location Variety Scope",
        dimension="SET",
        feature_type="ordinal",
        question=(
            "How many distinct physical locales does the story "
            "inhabit? Choose one: 'single' (one location), 'few' "
            "(2–3), 'many' (4–10), 'multiworld' (>10 "
            "distinct locations, or multiple worlds)."
        ),
        description=(
            "Rate the breadth of distinct physical locales in the "
            "narrative."
        ),
        response_options=("single", "few", "many", "multiworld"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=1.34, ai_mean=1.08,
                bundle="narrative_diversity",
            ),
        ),
        paper_table_row=26,
    ),
    CoreFeature(
        key="dialogue_to_narration_proportion",
        label="Dialogue-to-Narration Proportion",
        dimension="PER",
        feature_type="scale",
        question=(
            "What proportion of the text is direct dialogue versus "
            "narration? Score 1 (no dialogue) to 5 (dialogue "
            "dominates the page)."
        ),
        description=(
            "Rate the relative weight of dialogue versus narrative "
            "summary."
        ),
        response_options=("1", "2", "3", "4", "5"),
        signals=(
            FeatureSignal(
                option=None, leaning="human",
                human_mean=2.95, ai_mean=2.70,
                bundle="narrative_diversity",
            ),
        ),
        paper_table_row=28,
    ),
    CoreFeature(
        key="moral_polarity_toward_protagonist",
        label="Moral Polarity Toward Protagonist",
        dimension="PLT",
        feature_type="categorical",
        question=(
            "Does the narrative frame the protagonist's choices as "
            "morally clear or ambiguous? Choose one: "
            "'clearly_positive' (protagonist is framed sympathetically "
            "and morally right), 'ambivalent_or_mixed' (their choices "
            "are morally fraught), 'clearly_negative' (framed as "
            "morally wrong)."
        ),
        description=(
            "Classify the moral framing of the protagonist's choices. "
            "Humans elevate 'ambivalent_or_mixed'."
        ),
        response_options=(
            "clearly_positive",
            "ambivalent_or_mixed",
            "clearly_negative",
        ),
        signals=(
            FeatureSignal(
                option="ambivalent_or_mixed", leaning="human",
                human_mean=0.59, ai_mean=0.38,
                bundle="narrative_diversity",
            ),
        ),
        paper_table_row=30,
    ),
)


def iter_signals():
    """Yield (feature, signal_index, signal) for every feature signal.

    There are 33 yielded signals across 30 features (3 features carry
    two signals each).
    """
    for f in CORE_FEATURES:
        for i, s in enumerate(f.signals):
            yield f, i, s


# --- self-check at import time ------------------------------------
# Catch transcription mistakes early. Three integrity properties hold
# for the Russell et al. 2026 paper's Table 12 numbers and are cheap
# enough to assert on every import.

def _self_check() -> None:
    keys = {f.key for f in CORE_FEATURES}
    if len(keys) != len(CORE_FEATURES):
        raise RuntimeError("CORE_FEATURES contains duplicate keys")
    if len(CORE_FEATURES) != 30:
        raise RuntimeError(
            f"Expected 30 core features per the paper; got "
            f"{len(CORE_FEATURES)}"
        )
    total_signals = sum(len(f.signals) for f in CORE_FEATURES)
    if total_signals != 33:
        raise RuntimeError(
            f"Expected 33 total signals (Table 12 has 33 rows); got "
            f"{total_signals}"
        )
    for f in CORE_FEATURES:
        if f.dimension not in DIMENSION_LABELS:
            raise RuntimeError(
                f"Feature {f.key}: unknown dimension {f.dimension!r}"
            )
        for s in f.signals:
            if s.bundle not in BUNDLE_LABELS:
                raise RuntimeError(
                    f"Feature {f.key}: unknown bundle {s.bundle!r}"
                )
            # Paper means are bounded: probabilities in [0, 1] and
            # Likert/ordinal in [0, 5]. Loose check.
            for m in (s.human_mean, s.ai_mean):
                if not (0.0 <= m <= 5.0):
                    raise RuntimeError(
                        f"Feature {f.key}: mean {m} out of expected "
                        f"range"
                    )
            # Leaning must agree with the sign of (human - ai).
            gap_sign = (
                "human" if s.gap > 0
                else "ai" if s.gap < 0
                else None
            )
            if gap_sign is None:
                continue  # zero-gap is allowed but suspicious
            if gap_sign != s.leaning:
                raise RuntimeError(
                    f"Feature {f.key}: leaning {s.leaning!r} "
                    f"inconsistent with sign of gap "
                    f"{s.gap:+.2f}"
                )


_self_check()
