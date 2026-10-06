// CLAUDE> the user's supplement regime, as they gave it on 2026-10-06, with the known side effects of each supplement (NIH
// NCCIH fact sheets, trials and EU limits; a supplement taken again later in the day points back to its first row). Later
// the page tracks what was taken and shows blood pressure and weight from Withings.

type Supplement = { name: string; dose: string; how: string; why: string; effects: string }
type Moment = { name: string; when: string; supplements: Supplement[] }

const RED_YEAST_RICE = 'Same as statin drugs: muscle pain or weakness, liver and kidney damage. Stomach pain, nausea, diarrhoea. '
  + 'Many products contain citrinin, which can harm the kidneys. Same drug interactions as statins.'
const L_ARGININE = 'Bloating, nausea, diarrhoea. Blood pressure can drop too low with blood pressure medicines. '
  + 'Not advised after a heart attack.'
const OMEGA_3 = 'Fishy taste or burps, heartburn, nausea, diarrhoea. Higher chance of atrial fibrillation (irregular heartbeat), '
  + 'more so above 1 g a day. More bleeding with blood thinners.'
// CLAUDE> 6 pills of 2000 FU a day (the user, 2026-10-07), 2 at each moment with an empty stomach
const NATTOKINASE = 'More bleeding and bruising, more so with aspirin, blood thinners or omega-3. Stop before surgery or dental '
  + 'work. 12,000 FU a day is above the doses in most studies (2,000–4,000 FU a day; the highest about 10,800 FU).'

const REGIME: Moment[] = [
  { name: 'Morning', when: 'breakfast', supplements: [
    { name: 'Red yeast rice', dose: '1 pill (3 mg)', how: 'with food', why: 'LDL lowering', effects: RED_YEAST_RICE },
  ] },
  { name: 'Mid-morning', when: 'empty stomach', supplements: [
    { name: 'L-arginine', dose: '1.5–3 g', how: '', why: 'NO precursor, small BP drop', effects: L_ARGININE },
    { name: 'Nattokinase', dose: '2 pills (4000 FU)', how: '', why: 'mild fibrinolytic', effects: NATTOKINASE },
  ] },
  { name: 'Midday', when: 'lunch, with fat', supplements: [
    { name: 'Red yeast rice', dose: '1 pill (3 mg)', how: '', why: 'LDL lowering', effects: 'See Morning.' },
    { name: 'CoQ10', dose: '100–200 mg', how: '', why: 'offsets RYR depletion',
      effects: 'Mild: trouble sleeping, stomach upset. Can change the effect of warfarin and insulin.' },
    { name: 'Vitamin K2', dose: '180–360 µg MK-7', how: '', why: 'calcium to bone',
      effects: 'No known toxic effects. Stops warfarin and similar blood thinners from working.' },
    { name: 'Vitamin D3', dose: '1000–4000 IU', how: '', why: 'corrects deficiency, pairs with K2',
      effects: 'Rare at this dose; 4000 IU a day is the safe upper limit. Too much: high blood calcium (nausea, thirst, '
        + 'weakness, kidney stones).' },
    { name: 'Omega-3', dose: '1–2 g EPA+DHA', how: '', why: 'lowers triglycerides', effects: OMEGA_3 },
  ] },
  { name: 'Mid-afternoon', when: 'empty stomach', supplements: [
    { name: 'L-arginine', dose: '1.5–3 g', how: '', why: 'NO precursor, small BP drop', effects: 'See Mid-morning.' },
    { name: 'Nattokinase', dose: '2 pills (4000 FU)', how: '', why: 'mild fibrinolytic', effects: 'See Mid-morning.' },
  ] },
  { name: 'Evening', when: 'dinner, with fat', supplements: [
    { name: 'Red yeast rice', dose: '2 pills (6 mg)', how: '', why: 'LDL lowering, covers night synthesis peak',
      effects: 'See Morning.' },
    { name: 'Omega-3', dose: '1–2 g EPA+DHA', how: '', why: 'lowers triglycerides', effects: 'See Midday.' },
  ] },
  { name: 'Bedtime', when: 'empty stomach', supplements: [
    { name: 'Magnesium', dose: '300–400 mg glycinate', how: '', why: 'small BP drop, sleep',
      effects: 'Diarrhoea, nausea, cramps (less with glycinate). The safe upper limit from supplements is 350 mg a day. '
        + 'Avoid with kidney disease.' },
    { name: 'Nattokinase', dose: '2 pills (4000 FU)', how: '', why: 'mild fibrinolytic', effects: 'See Mid-morning.' },
  ] },
]

export default function HealthPage() {
  return (
    <section className="page accent-health health-page">
      <header className="page-head"><h1>Health</h1></header>
      <article className="sheet">
        <div className="sheet-head"><h2>Supplements</h2></div>
        <div className="health-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Supplement</th><th scope="col">Dose</th><th scope="col">How</th><th scope="col">Why</th>
                <th scope="col">Possible side effects</th>
              </tr>
            </thead>
            {REGIME.map(moment => (
              <tbody key={moment.name}>
                <tr className="health-moment">
                  <th scope="rowgroup" colSpan={5}>{moment.name} <span>{moment.when}</span></th>
                </tr>
                {moment.supplements.map(s => (
                  <tr key={s.name}>
                    <td className="health-name">{s.name}</td>
                    <td className="health-dose">{s.dose}</td>
                    <td>{s.how}</td>
                    <td>{s.why}</td>
                    <td className={s.effects.startsWith('See ') ? 'health-effects muted' : 'health-effects'}>{s.effects}</td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </article>
    </section>
  )
}
