# Local Translator: traduzione italiana per GW2

## Cos'è

Guild Wars 2 non esiste in italiano. **Local Translator** traduce in italiano dialoghi, missioni, oggetti, abilità, menu mentre giochi.
È gratuito e nessun dato lascia il tuo PC per effettuare la traduzione.

## Come funziona

Lavorano insieme tre elementi:

| Elemento             | Cos'è                           | Cosa fa                                                      |
| -------------------- | ------------------------------- | ------------------------------------------------------------ |
| **Nexus**            | il gestore di addon di Raidcore | permette di aggiungere addon a Guild Wars 2                  |
| **Text Translator**  | l'addon di Ideka                | legge i testi del gioco e mostra al loro posto le traduzioni |
| **Local Translator** | il modulo di questo progetto    | prepara e gestisce le traduzioni in italiano                 |

**Nexus** carica **Text Translator**, **Text Translator** chiede a **Local Translator** la traduzione di ogni testo e **Local Translator** la restituisce.

Le traduzioni arrivano da tre fonti, dalla più curata alla più semplice:

- la **patch**: traduzioni preparate in anticipo e riviste, pronte da subito;
- il **glossario**: le regole su come tradurre nomi e termini, per renderli coerenti in tutto il gioco;
- la **traduzione automatica locale**: quello che non è ancora nella patch viene tradotto al volo dal tuo PC, senza account né abbonamenti, e senza bisogno di una scheda video o di un processore potenti.

Patch e glossario si aggiornano da soli a ogni avvio del gioco.

> **Attenzione.** Si tratta di un lavoro in corso. Molte frasi sono ancora tradotte al volo dalla traduzione automatica ed a volte saranno strane o sbagliate. Migliorano man mano che il progetto va avanti.

## Requisiti

- Guild Wars 2 su Windows, con il gioco impostato in **inglese**
- [Nexus](https://raidcore.gg/Nexus), il gestore di addon.
- Circa **500 MB di RAM libera** mentre giochi e circa **250 MB di spazio** sul disco.

## Installazione

1. **Gioco in inglese.** Nel launcher di Guild Wars 2 clicca in alto a sinistra sul selettore della lingua (**LANGUAGE**) e scegli **EN**.

2. **Nexus.** Scarica **NexusInstaller.exe** da [raidcore.gg/Nexus](https://raidcore.gg/Nexus) e avvialo.
   Seleziona la cartella di installazione di Guild Wars 2 e premi il tasto in basso **Install Nexus**.

3. **Scarica Local Translator.** Scarica **[Local_Translator_0.12.0_IT_autosend.zip](https://github.com/NikeGipple/gw2-nexus-local-translator/releases/download/v0.12.0/Local_Translator_0.12.0_IT_autosend.zip)**.
   
   > **Verifica che sia l'ultima versione.** Il link porta alla versione 0.12.0. Apri la pagina dell'[ultima versione](../../releases/latest): se il numero è più alto, scarica da lì, nella sezione **Assets**, il file che finisce con **`_IT_autosend.zip`**.
   > 
   > Autosend è un'opzione che permette di collaborare con la raccolta dei testi del gioco. Se non vuoi inviare nulla leggi la sezione [Il modulo manda dati da qualche parte?](#domande-frequenti) nelle domande frequenti.

4. **Estrai lo zip nella cartella di Guild Wars 2.** Di solito è:
   
   - `C:\Program Files\Guild Wars 2`;
   - `C:\Program Files (x86)\Steam\steamapps\common\Guild Wars 2` se giochi da Steam.
   
   Per estrarre:
   
   - Clicca con il tasto destro sullo zip e scegli **Estrai...**.
   - Premi **Sfoglia...** e scegli la cartella di Guild Wars 2.
   
   > **Attenzione:** molti programmi propongono una cartella nuova con il nome dello zip. Va tolta: i file devono finire direttamente nella cartella di Guild Wars 2. I nomi delle voci possono cambiare in base al programma che usi per estrarre.
   
   La cartella `addons` dello zip si unisce a quella che hai già: gli altri addon non vengono toccati.
   
   **Controllo:** in `Guild Wars 2\addons` devi trovare:
   
   - il file `text_translator.dll`;
   - la cartella `text_translator`.

5. **Avvia il gioco.**
   
   - In alto a sinistra trovi una nuova icona a forma di X: è il menu principale di Nexus.
   - Se vuoi, puoi mettere Nexus in italiano: clicca sull'icona a forma di ingranaggio (**Options**) e in **Language** scegli **Italiano**.
   - Nella prima scheda in alto a sinistra, con l'icona della cassetta degli attrezzi (**Addon**), trovi Text Translator nella sezione **Installato** (Installed in eng). Clicca sul tasto **Carica** ( Load in eng).

6. **Primo Avvio**
   
   - Al **primo avvio** il modulo scarica il modello di traduzione: finché non ha finito, i testi restano in inglese.
   - Una volta scaricato il modello (di solito bastano pochi minuti) riavvia il gioco 
   - Goditi GW2 in italiano.
   - Enjoy :D

## Aggiornamenti

- **Patch e glossario** si aggiornano da soli all'avvio: non devi preoccuparti di tenere aggiornate le traduzioni.
- **Nuove versioni del modulo:** quando esce una nuova versione la trovi nell'[ultima versione](../../releases/latest).
  Scarica lo stesso tipo di zip che usi (`_IT_autosend.zip` o `_IT.zip`) ed estrailo nella cartella di Guild Wars 2 come nell'installazione, sostituendo i file vecchi. Attenzione: se aggiorni con lo zip `_IT.zip`, l'invio automatico si spegne.

## Problemi comuni

| Problema                                     | Cosa fare                                                                                                                                                                                                                                                                                                            |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tutto in inglese subito dopo l'installazione | Normale al primo avvio: il modello si sta ancora scaricando. Aspetta qualche minuto.                                                                                                                                                                                                                                 |
| Alcune frasi nuove restano in inglese        | La patch non copre ancora tutti i testi del gioco: le frasi nuove vengono tradotte al volo e le vedi in italiano solo quando riapri il dialogo o rivedi il testo una seconda volta. Per aiutarti puoi impostare da Text Translator un tasto per forzare questo processo e spegnere e attivare la traduzione al volo. |
| Il gioco scatta ogni pochi secondi           | Nelle opzioni di Text Translator (scheda **Settings**) attiva **Pause Refreshes**.                                                                                                                                                                                                                                   |
| Una traduzione è sbagliata o strana          | Può succedere: stiamo migliorando di continuo. Ci vorrà del tempo per tradurre al meglio 14 anni di contenuti, ma non preoccuparti: ci riusciremo! :D                                                                                                                                                                |

## Supporto

- **Discord dell'Arco del Leone**: https://discord.gg/EBTnpkFr2x. Scrivi in privato a **NikeGipple** per qualsiasi necessità collegata. Attraverso un ruolo dedicato potrai ricevere gli avvisi su nuove versioni e novità ma anche supporto e risposte.
- **Discord di Local Translator** (in inglese): https://discord.gg/8w2TZnNYG5

## Domande frequenti

**Devo pagare o creare un account?**\
No. È un progetto gratuito di giocatori volontari.

**Rischio il ban?**\
Local Translator non modifica i file del gioco e non gioca al posto tuo: cambia solo i testi che vedi sullo schermo. È però un progetto non ufficiale, non approvato da ArenaNet: lo usi sotto la tua responsabilità, come qualsiasi addon di Nexus.

**Il gioco diventa più lento?**\
La traduzione lavora solo quando arrivano testi nuovi, con priorità bassa, così il gioco viene sempre prima. Su PC con poca RAM potresti notare qualcosa.

**Il modulo manda dati da qualche parte?**\
La traduzione avviene sempre sul tuo PC. In più, il modulo si collega a GitHub per scaricare il modello, il glossario e la patch.

Se usi lo zip **autosend** (`_IT_autosend.zip`, come consigliato nell'installazione) stai aiutando il progetto a raccogliere i testi del gioco. Con l'opzione attiva il modulo manda in automatico l'elenco dei testi del gioco che incontri (solo il numero del testo, il testo inglese e quando l'hai visto; non salviamo nessun IP, niente nomi dei personaggi, niente account, niente traduzioni). Serve a sapere cosa tradurre nel modo migliore possibile. Scaricando questo zip accetti l'invio; dettagli nell'[informativa sulla privacy](PRIVACY.md) (in inglese).

Per non inviare niente usa lo zip normale (`_IT.zip`). Per spegnere l'invio dopo l'installazione, estrai lo zip `_IT.zip` sopra quello autosend e riavvia il gioco.

**Come si disinstalla?**

- Solo la traduzione italiana: cancella la cartella `addons\text_translator\modules\local_translator_it` nella cartella di Guild Wars 2.
- Anche Text Translator: rimuovilo dall'interfaccia di Nexus, oppure cancella il file `addons\text_translator.dll` e la cartella `addons\text_translator`.

---

*Local Translator è un progetto non ufficiale di giocatori, non collegato ad ArenaNet, NCSOFT, Raidcore o Ideka. Text Translator è un addon di Ideka. Licenze e crediti sono nel [README](README.md).*
