HVLK AI — dokumentace aplikace

Co aplikace aktuálně dělá

HVLK AI je v současné podobě nástroj pro monitorování a orientační analýzu amerického akciového trhu. Sestavuje seznam instrumentů, stahuje jejich historická denní tržní data, vypočítává základní statistiky a zobrazuje výsledky v Dashboardu.

Aplikace není automatický obchodní bot: sama nezadává nákupní ani prodejní příkazy, nepřipojuje se k brokerskému účtu a negarantuje obchodní signály ani zisk.

Jak funguje datový tok

1. Sestavení seznamu instrumentů — universe.py
  • Načítá seznamy z Nasdaq Trader (nasdaqlisted.txt a otherlisted.txt).
  • Doplňkově používá soubor SEC company_tickers_exchange.json.
  • Provádí pravidlové filtrování a převod symbolů pro datového poskytovatele.
  • Výsledek ukládá do data/universe.json.
  • Klasifikace je heuristická; nelze zaručit, že každý zahrnutý instrument je běžná akcie nebo že jsou všechny neakciové instrumenty vyloučeny.
2. Skenování trhu — scanner.py
  • Pro každý instrument žádá Yahoo Finance Chart endpoint o data za období jednoho roku s denním intervalem.
  • Odstraňuje duplicitní symboly, omezuje souběžné požadavky, používá timeouty a opakování některých neúspěšných požadavků.
  • Vypočítává poslední dostupnou denní zavírací cenu, změnu proti předchozí dostupné zavírací ceně, denní objem, průměrný objem až za 20 předchozích dostupných seancí a orientační obrat.
  • Výsledky ukládá do data/results.json a souhrn do data/market.json.
3. Automatické spuštění — GitHub Actions
  • Workflow .github/workflows/market-scan.yml je naplánovaný na pondělí až pátek v 06:17 UTC a podporuje také ruční spuštění.
  • Čas je uveden v UTC; místní čas se mění podle letního/zimního času.
  • Skenování a publikování jsou oddělené kroky. Výstupní JSON soubory se kontrolují a následně se publikují pouze schválené datové soubory.
  • Naplánovaný čas není zárukou úspěšného dokončení. Výpadek poskytovatele, omezení požadavků nebo chyba workflow mohou aktualizaci přerušit.
4. Zobrazení — index.html
  • Dashboard načítá data/market.json a data/results.json.
  • Zobrazuje přehled trhu, největší růsty a poklesy, objemy, odhadovaný obrat, neobvyklou aktivitu, seznam instrumentů a stav dat.
  • Prohlížeč se pokouší načíst data znovu každých 5 minut. To pouze obnovuje zobrazení dostupných souborů; nespouští nový sken trhu.
  • Pokud načtení dat selže, Dashboard může zobrazit chybu nebo nedostupné údaje.

Jak číst vypočítané hodnoty

• Změna v %: změna poslední dostupné denní zavírací ceny vůči předchozí dostupné denní zavírací ceně.
• Objem: objem uvedený poskytovatelem pro poslední dostupnou denní svíčku.
• Průměrný objem: průměr až z 20 předchozích dostupných denních seancí; nejde o záruku reprezentativního běžného objemu.
• Poměr objemu: poslední denní objem dělený vypočteným průměrným objemem. Neobvyklá aktivita je v aktuálním scanneru označena poměrem alespoň 2×.
• Odhadovaný obrat: zavírací cena násobená denním objemem. Je to orientační výpočet, nikoli ověřený peněžní tok ani přesný obrat burzy.
• Dividendový odhad: scanner sčítá až poslední čtyři dostupné historické dividendové události a vztahuje je k poslední zavírací ceně. Nejde o potvrzený budoucí dividendový výnos, prognózu ani výpočet čisté dividendy po zdanění.

Známé omezení dividendové sekce

Aktuální kód scanneru a Dashboardu používá pro dividendová data rozdílné názvy polí a Dashboard očekává i roční dividendu na akcii, kterou scanner v současné podobě samostatně nevytváří. Dividendová sekce proto nemusí zobrazovat správné nebo úplné výsledky. Před jejím používáním pro investiční rozhodnutí je nutné sjednotit datové schéma a ověřit výpočty. Ostatní části Dashboardu tím nejsou automaticky ověřeny jako bezchybné.

Aktualizace a kvalita dat

• Zdroj cenových dat je veřejný Yahoo Finance Chart endpoint; seznam instrumentů pochází z Nasdaq Trader a doplňkově SEC.
• Data jsou založena na denních historických cenách, nikoli na garantovaných živých intradenních kotacích.
• Čas vytvoření souboru (generated_at) je čas zpracování dat, ne nutně čas posledního obchodu na trhu.
• Některé symboly mohou vrátit chybu, zastaralá, neúplná nebo chybně klasifikovaná data. Úspěšné stažení samo o sobě nepotvrzuje správnost každé hodnoty.
• Poskytovatel může změnit rozhraní, omezit požadavky nebo odmítnout přístup. Při opakovaných odpovědích HTTP 429 může scanner skončit s částečnými výsledky.
• Pokrytí trhu se může měnit podle dostupnosti dat, změn seznamů a pravidel filtrování.

Rizika používání

1. Finanční riziko

• Výsledky, žebříčky a poměry jsou informační pomůcky, nikoli doporučení k nákupu či prodeji.
• Minulý pohyb ceny ani vysoký objem nepředpovídají spolehlivě budoucí vývoj.
• Instrument s mimořádným procentním růstem může být velmi volatilní, málo likvidní nebo ovlivněný jednorázovou událostí.
• Obchodování může vést ke ztrátě části nebo celého investovaného kapitálu. Při použití páky mohou být ztráty ještě vyšší.
• Dashboard nepočítá individuální toleranci rizika, investiční horizont, poplatky, spread, skluz, daňové dopady ani vhodnou velikost pozice.

2. Riziko chybných nebo zastaralých údajů

• Data mohou být zpožděná, chybějící, upravená poskytovatelem nebo nesprávně přiřazená k symbolu.
• Denní data nemusejí zachytit aktuální pohyb ceny během obchodní seance.
• Pravidlové filtrování instrumentů není oficiální klasifikace cenných papírů.
• Odhad dividendy nemusí odpovídat budoucím výplatám a v současné verzi má navíc výše popsaný problém s kompatibilitou polí v Dashboardu.

3. Technické a provozní riziko

• GitHub Actions může selhat, zpozdit se nebo narazit na limity poskytovatele.
• Dashboard může načítat poslední publikované soubory, i když nový sken neproběhl.
• Úspěšné načtení stránky neznamená, že jsou data aktuální nebo úplná.
• Změna formátu dat může způsobit chyby zobrazení nebo nesprávné výpočty.

4. Bezpečnostní a přístupová rizika

• Před zveřejněním repozitáře nikdy neukládejte do zdrojového kódu přístupové tokeny, hesla, API klíče ani jiné tajné údaje.
• Používejte pouze nezbytná oprávnění GitHub Actions a kontrolujte změny workflow před jejich sloučením.
• Nepoužívejte data ani kód z neověřených zdrojů bez kontroly.

Doporučený bezpečný postup

1. Před rozhodnutím ověřte cenu a události u nezávislého důvěryhodného zdroje nebo u svého brokera.
2. Zkontrolujte čas skenu, počet úspěšných a neúspěšných požadavků a pokrytí trhu.
3. Neprovádějte obchod jen na základě jedné hodnoty, pořadí v tabulce nebo neobvyklého pohybu.
4. Ověřte likviditu, spread, zprávy emitenta a vlastní plán řízení rizika.
5. Otestujte změny na historických datech nebo v simulaci předtím, než je použijete při reálném obchodování.
6. Dividendovou sekci považujte za neověřenou, dokud nebude opravena a otestována.

Omezení a odpovědnost

Aplikace je poskytována „tak, jak je“, bez záruky přesnosti, dostupnosti nebo vhodnosti pro konkrétní účel. Výstupy slouží pouze k informativnímu a výzkumnému použití a nepředstavují investiční, právní ani daňové poradenství. Za vlastní investiční a obchodní rozhodnutí odpovídá uživatel.

────────

Struktura důležitých souborů

.
├── .github/
│   └── workflows/
│       └── market-scan.yml
├── data/
│   ├── universe.json
│   ├── results.json
│   └── market.json
├── universe.py
├── scanner.py
└── index.html

• universe.py — sestavení seznamu instrumentů.
• scanner.py — stažení a výpočet tržních statistik.
• data/universe.json — vstupní seznam instrumentů.
• data/results.json — výsledky jednotlivých instrumentů.
• data/market.json — souhrny a žebříčky pro Dashboard.
• index.html — webové rozhraní.
• .github/workflows/market-scan.yml — plánování, spuštění a publikování dat.
