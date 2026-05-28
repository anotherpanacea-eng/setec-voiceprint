You are a narrative analysis expert. Extract a comprehensive outline from the provided narrative text by
answering the questions below. Analyze the text systematically and provide specific evidence for each element
identified.

## Instructions
- Be objective and avoid interpretation beyond what the text explicitly or implicitly conveys
- Use null for information not present in the narrative or fields with no applicable items
- For trajectories and sequences, use arrows (->) to show progression: "state1 -> state2 -> state3". Remember,
   trajectories and sequences are not always linear, but always have to be complete.
- Keep descriptions concise but specific.
- **Scale guidance**:
  - **Global** fields require story-level analysis across the entire narrative
  - **Local** fields require scene-level or moment-specific analysis (indicate which scene/moment when
    relevant)

## Narrative
{narrative_text}

## Extraction object schema
Return a JSON object with this structure:

- `story`
  - `agents`
    - `major_characters`: list only major characters who drive the plot or have significant narrative
      importance
      - each item includes:
        - `name` `(string) [GLOBAL]`: use the character's full name as-is
        - `role` `(string) [GLOBAL]`: narrative and functional role, max 2 short clauses
        - `attributes` `(array of strings) [GLOBAL]`: descriptive traits or phrases
        - `emotion_trajectory` `(string) [GLOBAL]`: initial emotion -> progression -> final emotion
        - `motivation_trajectory` `(string) [GLOBAL]`: initial motivation -> progression -> final motivation
        - `trope` `(string) [GLOBAL]`: trope or archetype, if any
    - `supporting_characters`: meaningful minor characters who are not central to the plot
      - each item includes:
        - `name` `(string) [GLOBAL]`
        - `description` `(string) [GLOBAL]`: one-line role/significance summary
  - `social_network`
    - `relationships` `(list) [GLOBAL]`: enduring bonds, formatted as `A-B: relationship type and quality`
  - `events`
    - `sequence`: ordered list of concrete beat-level events `[LOCAL]`
      - each item includes `who`, `where`, `what`, and `when`
    - `causality` `(list) [GLOBAL]`: causal links formatted as `event1 -> event2: explanation`
    - `narrative_schema` `(string) [GLOBAL]`: higher-level pattern such as quest, revenge, or coming-of-age
  - `plot`
    - `themes` `(list) [GLOBAL]`
    - `summary` `(string) [GLOBAL]`: 2-3 sentence plot summary
    - `moral` `(string) [GLOBAL]`: one sentence if signaled; else `null`
    - `central_obstacle` `(string) [GLOBAL]`
    - `central_conflict` `(string) [GLOBAL]`
    - `narrative_archetype` `(string) [GLOBAL]`
    - `plot_arc` `(string) [GLOBAL]`: e.g., `rising action -> climax -> falling action`
  - `setting`
    - `locations` `(list) [LOCAL/GLOBAL]`: include scene-level and story-level locations, with scope noted
    - `time_period` `(string) [GLOBAL]`
    - `atmosphere` `(string) [GLOBAL]`

- `discourse`
  - `revelation`
    - `suspense` `(string) [GLOBAL]`: what key information is withheld?
    - `curiosity` `(string) [GLOBAL]`: what causal antecedents are withheld?
    - `surprises` `(list) [GLOBAL]`: what was revealed, and when?
  - `temporal_order`
    - `structure` `(string) [GLOBAL]`: choose `linear`, `nonlinear`, or `mixed`
    - `duration` `(string) [GLOBAL]`: overall time span
    - `flashbacks` `(list) [LOCAL]`: note which scenes are flashbacks
    - `time_jumps` `(list) [LOCAL]`: ellipses or leaps in time/place, with scene references
    - `scene_duration` `(list) [LOCAL]`: approximate duration of major scenes

- `narration`
  - `perspective`
    - `point_of_view` `(string) [GLOBAL]`: choose `1st person`, `2nd person`, `3rd person limited`, or
      `3rd person omniscient`
    - `focalization` `(list) [LOCAL]`: whose perspective we occupy by scene/section
    - `dialogue_speakers` `(list) [LOCAL]`: named speakers by scene/section
  - `style`
    - `allusions` `(list) [LOCAL]`: allusions plus scene/section context
    - `figurative_language` `(list) [LOCAL]`: examples plus scene context
    - `imagery` `(list) [LOCAL]`: vivid sensory descriptions plus scene context
    - `sentence_complexity` `(string) [GLOBAL]`
    - `evaluative_language` `(list) [LOCAL]`: judgmental/evaluative language plus scene context
