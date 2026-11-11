"""Translations for settings navigation and F12 screenshots.

Kept separately because the shared translation source is not writable in this checkout.
"""
EXTRA = {
    'Done': {
        'de': 'Fertig',
        'fr': 'Terminé',
        'es': 'Listo',
        'it': 'Fatto',
        'pt': 'Concluído',
        'nl': 'Klaar',
        'pl': 'Gotowe',
    },
    'Could not save screenshot note': {
        'de': 'Notiz zum Bildschirmfoto konnte nicht gespeichert werden',
        'fr': 'Impossible d’enregistrer la note de capture',
        'es': 'No se pudo guardar la nota de la captura',
        'it': 'Impossibile salvare la nota della schermata',
        'pt': 'Não foi possível salvar a nota da captura',
        'nl': 'Notitie bij schermafbeelding kon niet worden opgeslagen',
        'pl': 'Nie można zapisać notatki do zrzutu ekranu',
    },
    'No setting matches your search.': {
        'de': 'Keine Einstellung passt zu deiner Suche.',
        'fr': 'Aucun paramètre ne correspond à votre recherche.',
        'es': 'Ningún ajuste coincide con tu búsqueda.',
        'it': 'Nessuna impostazione corrisponde alla ricerca.',
        'pt': 'Nenhuma configuração corresponde à sua busca.',
        'nl': 'Geen instelling komt overeen met je zoekopdracht.',
        'pl': 'Żadne ustawienie nie pasuje do wyszukiwania.',
    },
    'General': {
        'de': 'Allgemein',
        'fr': 'Général',
        'es': 'General',
        'it': 'Generale',
        'pt': 'Geral',
        'nl': 'Algemeen',
        'pl': 'Ogólne',
    },
    'Screenshots': {
        'de': 'Bildschirmfotos',
        'fr': 'Captures d’écran',
        'es': 'Capturas de pantalla',
        'it': 'Schermate',
        'pt': 'Capturas de tela',
        'nl': 'Schermafbeeldingen',
        'pl': 'Zrzuty ekranu',
    },
    'F12 saves a screenshot': {
        'de': 'F12 speichert ein Bildschirmfoto',
        'fr': 'F12 enregistre une capture d’écran',
        'es': 'F12 guarda una captura de pantalla',
        'it': 'F12 salva una schermata',
        'pt': 'F12 salva uma captura de tela',
        'nl': 'F12 slaat een schermafbeelding op',
        'pl': 'F12 zapisuje zrzut ekranu',
    },
    'Save a picture of the Theme Manager window while it is open.': {
        'de': 'Ein Bild des geöffneten Theme-Manager-Fensters speichern.',
        'fr': 'Enregistrer une image de la fenêtre ouverte de Theme Manager.',
        'es': 'Guardar una imagen de la ventana abierta de Theme Manager.',
        'it': 'Salva un’immagine della finestra aperta di Theme Manager.',
        'pt': 'Salva uma imagem da janela aberta do Theme Manager.',
        'nl': 'Sla een afbeelding van het geopende Theme Manager-venster op.',
        'pl': 'Zapisz obraz otwartego okna Theme Managera.',
    },
    'Screenshots go to': {
        'de': 'Bildschirmfotos speichern in',
        'fr': 'Enregistrer les captures dans',
        'es': 'Guardar capturas en',
        'it': 'Salva le schermate in',
        'pt': 'Salvar capturas em',
        'nl': 'Schermafbeeldingen opslaan in',
        'pl': 'Zapisuj zrzuty w',
    },
    'Choose folder…': {
        'de': 'Ordner wählen…',
        'fr': 'Choisir un dossier…',
        'es': 'Elegir carpeta…',
        'it': 'Scegli cartella…',
        'pt': 'Escolher pasta…',
        'nl': 'Map kiezen…',
        'pl': 'Wybierz folder…',
    },
    'Find in Settings': {
        'de': 'In Einstellungen suchen',
        'fr': 'Rechercher dans les paramètres',
        'es': 'Buscar en Ajustes',
        'it': 'Cerca nelle impostazioni',
        'pt': 'Buscar nas configurações',
        'nl': 'Zoeken in instellingen',
        'pl': 'Szukaj w ustawieniach',
    },
    'Choose screenshot folder': {
        'de': 'Ordner für Bildschirmfotos wählen',
        'fr': 'Choisir le dossier des captures d’écran',
        'es': 'Elegir carpeta de capturas',
        'it': 'Scegli la cartella delle schermate',
        'pt': 'Escolher pasta das capturas',
        'nl': 'Map voor schermafbeeldingen kiezen',
        'pl': 'Wybierz folder zrzutów ekranu',
    },
    'Could not save screenshot': {
        'de': 'Bildschirmfoto konnte nicht gespeichert werden',
        'fr': 'Impossible d’enregistrer la capture',
        'es': 'No se pudo guardar la captura',
        'it': 'Impossibile salvare la schermata',
        'pt': 'Não foi possível salvar a captura',
        'nl': 'Schermafbeelding kon niet worden opgeslagen',
        'pl': 'Nie można zapisać zrzutu ekranu',
    },
    'The monitor position is unavailable.': {
        'de': 'Die Monitorposition ist nicht verfügbar.',
        'fr': 'La position de l’écran est indisponible.',
        'es': 'La posición del monitor no está disponible.',
        'it': 'La posizione del monitor non è disponibile.',
        'pt': 'A posição do monitor não está disponível.',
        'nl': 'De monitorpositie is niet beschikbaar.',
        'pl': 'Położenie monitora jest niedostępne.',
    },
    'Screenshot saved': {
        'de': 'Bildschirmfoto gespeichert',
        'fr': 'Capture enregistrée',
        'es': 'Captura guardada',
        'it': 'Schermata salvata',
        'pt': 'Captura salva',
        'nl': 'Schermafbeelding opgeslagen',
        'pl': 'Zrzut ekranu zapisany',
    },
    'Default': {
        'de': 'Standard',
        'fr': 'Par défaut',
        'es': 'Predeterminado',
        'it': 'Predefinito',
        'pt': 'Padrão',
        'nl': 'Standaard',
        'pl': 'Domyślnie',
    },
}

def install(strings):
    for key, translations in EXTRA.items():
        for code, value in translations.items():
            strings.setdefault(code, {})[key] = value
    # Keep the new controls available in every supported interface language.
    for key in NEW_KEYS:
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = NEW_TRANSLATIONS[key][code]
    for key, values in REVIEW_TRANSLATIONS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = values[code]
    for key, values in WORKSPACE_TRANSLATIONS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = values[code]
    for key, values in ASK_TRANSLATIONS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = values[code]
    for key, values in LOOK_TRANSLATIONS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = values[code]
    for key, values in CORNER_TRANSLATIONS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {})[key] = values[code]
    from workspace_i18n import STRINGS as WORKSPACE_TABS
    for key, values in WORKSPACE_TABS.items():
        for code in ('de', 'fr', 'es', 'it', 'pt', 'nl', 'pl'):
            strings.setdefault(code, {}).setdefault(key, values[code])

REVIEW_TRANSLATIONS = {
    'Drag to rearrange · Alt+←/→': dict(de='Zum Umordnen ziehen · Alt+←/→', fr='Glisser pour réorganiser · Alt+←/→', es='Arrastra para reordenar · Alt+←/→', it='Trascina per riordinare · Alt+←/→', pt='Arraste para reorganizar · Alt+←/→', nl='Sleep om te herschikken · Alt+←/→', pl='Przeciągnij, aby zmienić kolejność · Alt+←/→'),
    'Header buttons': dict(de='Kopfzeilen-Schaltflächen', fr='Boutons d’en-tête', es='Botones de encabezado', it='Pulsanti della barra superiore', pt='Botões do cabeçalho', nl='Knoppen in de kop', pl='Przyciski nagłówka'),
    'Resets language, corners, sorting, header button order, picker sync, schedule, wallpaper rotation, screenshot key and folder, and web search for wallpapers (on). Keeps favorites, hidden themes, shortcuts and saved workspace assignments; themes per workspace turn off.': dict(de='Setzt Sprache, Ecken, Sortierung, Reihenfolge der Kopfzeilen-Schaltflächen, Auswahl-Abgleich, Zeitplan, Hintergrundwechsel, Screenshot-Taste und -Ordner sowie die Websuche nach Hintergründen (an) zurück. Favoriten, versteckte Themes, Kürzel und Arbeitsbereichszuweisungen bleiben; Themes pro Arbeitsbereich werden ausgeschaltet.', fr='Réinitialise la langue, les coins, le tri, l’ordre des boutons d’en-tête, la synchronisation du sélecteur, le programme, la rotation des fonds, la touche et le dossier des captures et la recherche web de fonds (activée). Conserve les favoris, thèmes masqués, raccourcis et attributions aux espaces ; les thèmes par espace sont désactivés.', es='Restablece idioma, esquinas, orden, orden de los botones del encabezado, sincronización del selector, horario, rotación de fondos, tecla y carpeta de capturas y búsqueda web de fondos (activada). Conserva favoritos, temas ocultos, atajos y asignaciones de espacios; desactiva los temas por espacio.', it='Ripristina lingua, angoli, ordinamento, ordine dei pulsanti della barra superiore, sincronizzazione del selettore, programma, rotazione degli sfondi, tasto e cartella delle schermate e ricerca web di sfondi (attiva). Conserva preferiti, temi nascosti, scorciatoie e assegnazioni degli spazi; disattiva i temi per spazio.', pt='Redefine idioma, cantos, ordenação, ordem dos botões do cabeçalho, sincronização do seletor, programação, rotação de papéis de parede, tecla e pasta das capturas e busca na web por papéis de parede (ativada). Mantém favoritos, temas ocultos, atalhos e atribuições de áreas de trabalho; desativa temas por área.', nl='Herstelt taal, hoeken, sortering, volgorde van de knoppen in de kop, synchronisatie van de kiezer, schema, achtergrondrotatie, toets en map voor schermafbeeldingen en zoeken naar achtergronden op het web (aan). Behoudt favorieten, verborgen thema’s, sneltoetsen en werkbladtoewijzingen; thema’s per werkblad worden uitgeschakeld.', pl='Przywraca język, rogi, sortowanie, kolejność przycisków nagłówka, synchronizację wybieraka, harmonogram, zmianę tapet, klawisz i folder zrzutów oraz wyszukiwanie tapet w sieci (włączone). Zachowuje ulubione, ukryte motywy, skróty i przypisania obszarów; wyłącza motywy dla obszarów.'),
    'Switching takes a moment and changes every monitor. Leaving the workspace goes back to the theme you used before.': dict(de='Der Wechsel dauert einen Moment und betrifft alle Monitore. Beim Verlassen des Arbeitsbereichs kehrt das vorherige Theme zurück.', fr='Le changement prend un instant et touche tous les écrans. En quittant cet espace, le thème précédent revient.', es='El cambio tarda un momento y afecta a todos los monitores. Al salir del espacio vuelve el tema anterior.', it='Il cambio richiede un momento e riguarda tutti gli schermi. Uscendo dallo spazio torna il tema precedente.', pt='A troca leva um momento e afeta todos os monitores. Ao sair da área, o tema anterior volta.', nl='De wissel duurt even en geldt voor alle schermen. Bij het verlaten van het werkblad keert het vorige thema terug.', pl='Zmiana trwa chwilę i obejmuje wszystkie monitory. Po opuszczeniu obszaru wraca poprzedni motyw.'),
    'Could not apply the preset theme.': dict(de='Das Preset-Theme konnte nicht angewendet werden.', fr='Impossible d’appliquer le thème du préréglage.', es='No se pudo aplicar el tema del preajuste.', it='Impossibile applicare il tema della preimpostazione.', pt='Não foi possível aplicar o tema do preset.', nl='Het thema van de voorinstelling kon niet worden toegepast.', pl='Nie można zastosować motywu presetu.'),
    'A preset with this name already exists.': dict(de='Ein Preset mit diesem Namen gibt es bereits.', fr='Un préréglage porte déjà ce nom.', es='Ya existe un preajuste con este nombre.', it='Esiste già una preimpostazione con questo nome.', pt='Já existe um preset com esse nome.', nl='Er bestaat al een voorinstelling met deze naam.', pl='Preset o tej nazwie już istnieje.'),
    'Assign preset {0} to workspace': dict(de='Preset {0} einem Arbeitsbereich zuweisen', fr='Attribuer le préréglage {0} à un espace de travail', es='Asignar el preajuste {0} al espacio de trabajo', it='Assegna la preimpostazione {0} allo spazio di lavoro', pt='Atribuir o preset {0} à área de trabalho', nl='Voorinstelling {0} aan werkblad toewijzen', pl='Przypisz preset {0} do obszaru roboczego'),
    'Choose a local file': dict(de='Lokale Datei auswählen', fr='Choisir un fichier local', es='Elige un archivo local', it='Scegli un file locale', pt='Escolha um arquivo local', nl='Kies een lokaal bestand', pl='Wybierz plik lokalny'),
    'Choose a local folder': dict(de='Lokalen Ordner auswählen', fr='Choisir un dossier local', es='Elige una carpeta local', it='Scegli una cartella locale', pt='Escolha uma pasta local', nl='Kies een lokale map', pl='Wybierz folder lokalny'),
    'Could not open folder': dict(de='Ordner konnte nicht geöffnet werden', fr='Impossible d’ouvrir le dossier', es='No se pudo abrir la carpeta', it='Impossibile aprire la cartella', pt='Não foi possível abrir a pasta', nl='Map kon niet worden geopend', pl='Nie można otworzyć folderu'),
    'Delete preset {0}': dict(de='Preset {0} löschen', fr='Supprimer le préréglage {0}', es='Eliminar el preajuste {0}', it='Elimina la preimpostazione {0}', pt='Excluir o preset {0}', nl='Voorinstelling {0} verwijderen', pl='Usuń preset {0}'),
    'Enter a preset name.': dict(de='Namen für das Preset eingeben.', fr='Saisissez un nom de préréglage.', es='Escribe un nombre para el preajuste.', it='Inserisci un nome per la preimpostazione.', pt='Digite um nome para o preset.', nl='Voer een naam voor de voorinstelling in.', pl='Wpisz nazwę presetu.'),
    'Font unavailable': dict(de='Schrift nicht verfügbar', fr='Police indisponible', es='Fuente no disponible', it='Carattere non disponibile', pt='Fonte indisponível', nl='Lettertype niet beschikbaar', pl='Czcionka niedostępna'),
    'Invalid backup files.': dict(de='Ungültige Sicherungsdateien.', fr='Fichiers de sauvegarde invalides.', es='Archivos de copia no válidos.', it='File di backup non validi.', pt='Arquivos de backup inválidos.', nl='Ongeldige back-upbestanden.', pl='Nieprawidłowe pliki kopii.'),
    'Invalid preset': dict(de='Ungültiges Preset', fr='Préréglage invalide', es='Preajuste no válido', it='Preimpostazione non valida', pt='Preset inválido', nl='Ongeldige voorinstelling', pl='Nieprawidłowy preset'),
    'Invalid presets in backup.': dict(de='Ungültige Presets in der Sicherung.', fr='Préréglages invalides dans la sauvegarde.', es='Preajustes no válidos en la copia.', it='Preimpostazioni non valide nel backup.', pt='Presets inválidos no backup.', nl='Ongeldige voorinstellingen in de back-up.', pl='Nieprawidłowe presety w kopii.'),
    'Invalid shortcuts in backup.': dict(de='Ungültige Tastenkürzel in der Sicherung.', fr='Raccourcis invalides dans la sauvegarde.', es='Atajos no válidos en la copia.', it='Scorciatoie non valide nel backup.', pt='Atalhos inválidos no backup.', nl='Ongeldige sneltoetsen in de back-up.', pl='Nieprawidłowe skróty w kopii.'),
    'Menu line missing': dict(de='Menüeintrag fehlt', fr='Ligne de menu manquante', es='Falta la línea del menú', it='Riga del menu mancante', pt='Linha do menu ausente', nl='Menuregel ontbreekt', pl='Brak wpisu menu'),
    'No wallpaper': dict(de='Kein Hintergrund', fr='Aucun fond d’écran', es='Sin fondo', it='Nessuno sfondo', pt='Sem papel de parede', nl='Geen achtergrond', pl='Bez tapety'),
    'Open folder': dict(de='Ordner öffnen', fr='Ouvrir le dossier', es='Abrir carpeta', it='Apri cartella', pt='Abrir pasta', nl='Map openen', pl='Otwórz folder'),
    'Replace': dict(de='Ersetzen', fr='Remplacer', es='Reemplazar', it='Sostituisci', pt='Substituir', nl='Vervangen', pl='Zastąp'),
    'Replace “{0}”?': dict(de='„{0}“ ersetzen?', fr='Remplacer « {0} » ?', es='¿Reemplazar «{0}»?', it='Sostituire «{0}»?', pt='Substituir “{0}”?', nl='‘{0}’ vervangen?', pl='Zastąpić „{0}”?'),
    'Replaces {0}': dict(de='Ersetzt {0}', fr='Remplace {0}', es='Reemplaza {0}', it='Sostituisce {0}', pt='Substitui {0}', nl='Vervangt {0}', pl='Zastępuje {0}'),
    'Replace your settings, favorites, hidden themes, workspace assignments, presets and shortcuts with this backup? Your current state is saved first as {0}.': dict(de='Einstellungen, Favoriten, versteckte Themes, Arbeitsbereichszuweisungen, Presets und Tastenkürzel ersetzen? Der aktuelle Stand wird zuerst als {0} gesichert.', fr='Remplacer réglages, favoris, thèmes masqués, espaces attribués, préréglages et raccourcis ? L’état actuel sera d’abord enregistré sous {0}.', es='¿Reemplazar ajustes, favoritos, temas ocultos, espacios asignados, preajustes y atajos? El estado actual se guardará antes como {0}.', it='Sostituire impostazioni, preferiti, temi nascosti, spazi assegnati, preimpostazioni e scorciatoie? Lo stato attuale sarà prima salvato come {0}.', pt='Substituir configurações, favoritos, temas ocultos, áreas atribuídas, presets e atalhos? O estado atual será salvo primeiro como {0}.', nl='Instellingen, favorieten, verborgen thema’s, werkbladen, voorinstellingen en sneltoetsen vervangen? De huidige staat wordt eerst opgeslagen als {0}.', pl='Zastąpić ustawienia, ulubione, ukryte motywy, przypisania obszarów, presety i skróty? Bieżący stan zostanie najpierw zapisany jako {0}.'),
    'The current theme is unavailable.': dict(de='Das aktuelle Theme ist nicht verfügbar.', fr='Le thème actuel est indisponible.', es='El tema actual no está disponible.', it='Il tema attuale non è disponibile.', pt='O tema atual está indisponível.', nl='Het huidige thema is niet beschikbaar.', pl='Bieżący motyw jest niedostępny.'),
    'Theme hook missing': dict(de='Theme-Hook fehlt', fr='Hook de thème manquant', es='Falta el hook del tema', it='Hook del tema mancante', pt='Hook do tema ausente', nl='Thema-hook ontbreekt', pl='Brak hooka motywu'),
    'Theme missing': dict(de='Theme fehlt', fr='Thème manquant', es='Falta el tema', it='Tema mancante', pt='Tema ausente', nl='Thema ontbreekt', pl='Brak motywu'),
    "Turn on ‘Hidden in Omarchy's pickers’ first.": dict(de='Zuerst „In Omarchys Auswahl ausblenden“ einschalten.', fr='Activez d’abord « Masquer dans les sélecteurs Omarchy ».', es='Activa primero «Ocultar en los selectores de Omarchy».', it='Attiva prima «Nascondi nei selettori Omarchy».', pt='Ative primeiro «Ocultar nos seletores do Omarchy».', nl='Schakel eerst ‘Verbergen in Omarchy-kiezers’ in.', pl='Najpierw włącz ukrywanie w wybierakach Omarchy.'),
    'Unsupported backup format.': dict(de='Sicherungsformat nicht unterstützt.', fr='Format de sauvegarde non pris en charge.', es='Formato de copia no compatible.', it='Formato di backup non supportato.', pt='Formato de backup não suportado.', nl='Back-upformaat wordt niet ondersteund.', pl='Nieobsługiwany format kopii.'),
    'Use a name of up to 60 printable characters.': dict(de='Namen mit höchstens 60 druckbaren Zeichen verwenden.', fr='Utilisez un nom de 60 caractères imprimables maximum.', es='Usa un nombre de hasta 60 caracteres imprimibles.', it='Usa un nome di massimo 60 caratteri stampabili.', pt='Use um nome de até 60 caracteres imprimíveis.', nl='Gebruik een naam van maximaal 60 afdrukbare tekens.', pl='Użyj nazwy do 60 drukowalnych znaków.'),
    'Wallpaper missing': dict(de='Hintergrund fehlt', fr='Fond d’écran manquant', es='Falta el fondo', it='Sfondo mancante', pt='Papel de parede ausente', nl='Achtergrond ontbreekt', pl='Brak tapety'),
    'Workspace {0} cleared': dict(de='Arbeitsbereich {0} geleert', fr='Espace {0} libéré', es='Espacio {0} liberado', it='Spazio {0} svuotato', pt='Área {0} limpa', nl='Werkblad {0} gewist', pl='Wyczyszczono obszar {0}'),
    'Your own style.theme override is in omarchy-menu.jsonc': dict(de='Eigener style.theme-Eintrag in omarchy-menu.jsonc vorhanden', fr='Votre remplacement style.theme est dans omarchy-menu.jsonc', es='Tu ajuste style.theme está en omarchy-menu.jsonc', it='Il tuo override style.theme è in omarchy-menu.jsonc', pt='Sua substituição style.theme está em omarchy-menu.jsonc', nl='Eigen style.theme-overschrijving staat in omarchy-menu.jsonc', pl='Własne nadpisanie style.theme jest w omarchy-menu.jsonc'),
    '{0} hidden themes are still in the picker': dict(de='{0} versteckte Themes sind noch in der Auswahl', fr='{0} thèmes masqués restent dans le sélecteur', es='{0} temas ocultos siguen en el selector', it='{0} temi nascosti sono ancora nel selettore', pt='{0} temas ocultos ainda estão no seletor', nl='{0} verborgen thema’s staan nog in de kiezer', pl='{0} ukrytych motywów nadal jest w wybieraku'),
}

NEW_TRANSLATIONS = {
    'Apply the preset theme too, or choose a preset for the active theme.': dict(de='Auch das Theme der Voreinstellung anwenden oder eine Voreinstellung für das aktive Theme wählen.', fr='Appliquez aussi le thème du préréglage ou choisissez un préréglage pour le thème actif.', es='Aplica también el tema del preajuste o elige uno para el tema activo.', it='Applica anche il tema della preimpostazione o scegline una per il tema attivo.', pt='Aplique também o tema da preset ou escolha uma preset para o tema atual.', nl='Pas ook het thema van de voorinstelling toe of kies er een voor het actieve thema.', pl='Zastosuj także motyw presetu albo wybierz preset dla aktywnego motywu.'),
    'Presets': dict(de='Voreinstellungen', fr='Préréglages', es='Preajustes', it='Preimpostazioni', pt='Presets', nl='Voorinstellingen', pl='Presety'),
    'Preset name': dict(de='Name der Voreinstellung', fr='Nom du préréglage', es='Nombre del preajuste', it='Nome preimpostazione', pt='Nome da preset', nl='Naam voorinstelling', pl='Nazwa presetu'),
    'Save current': dict(de='Aktuelles speichern', fr='Enregistrer actuel', es='Guardar actual', it='Salva attuale', pt='Salvar atual', nl='Huidige opslaan', pl='Zapisz bieżące'),
    'Apply': dict(de='Anwenden', fr='Appliquer', es='Aplicar', it='Applica', pt='Aplicar', nl='Toepassen', pl='Zastosuj'),
    'Delete preset': dict(de='Voreinstellung löschen', fr='Supprimer le préréglage', es='Eliminar preajuste', it='Elimina preimpostazione', pt='Excluir preset', nl='Voorinstelling verwijderen', pl='Usuń preset'),
    'No presets yet.': dict(de='Noch keine Voreinstellungen.', fr='Aucun préréglage.', es='Aún no hay preajustes.', it='Nessuna preimpostazione.', pt='Ainda sem presets.', nl='Nog geen voorinstellingen.', pl='Brak presetów.'),
    'Theme': dict(de='Theme', fr='Thème', es='Tema', it='Tema', pt='Tema', nl='Thema', pl='Motyw'),
    'Wallpaper': dict(de='Hintergrund', fr='Fond d’écran', es='Fondo de pantalla', it='Sfondo', pt='Papel de parede', nl='Achtergrond', pl='Tapeta'),
    'Theme + wallpaper': dict(de='Theme + Hintergrund', fr='Thème + fond d’écran', es='Tema + fondo', it='Tema + sfondo', pt='Tema + papel de parede', nl='Thema + achtergrond', pl='Motyw + tapeta'),
    'Assign to workspace': dict(de='Arbeitsbereich zuweisen', fr='Attribuer à l’espace de travail', es='Asignar al espacio de trabajo', it='Assegna allo spazio di lavoro', pt='Atribuir à área de trabalho', nl='Aan werkblad toewijzen', pl='Przypisz do obszaru roboczego'),
    'Use on workspace…': dict(de='Auf Arbeitsbereich verwenden…', fr='Utiliser sur l’espace de travail…', es='Usar en espacio de trabajo…', it='Usa nello spazio di lavoro…', pt='Usar na área de trabalho…', nl='Gebruiken op werkblad…', pl='Użyj w obszarze roboczym…'),
    'A theme for each workspace': dict(de='Ein Theme pro Arbeitsbereich', fr='Un thème par espace de travail', es='Un tema por espacio de trabajo', it='Un tema per spazio di lavoro', pt='Um tema por área de trabalho', nl='Een thema per werkblad', pl='Motyw dla każdego obszaru'),
    'Integration and backup': dict(de='Integration und Sicherung', fr='Intégration et sauvegarde', es='Integración y copia de seguridad', it='Integrazione e backup', pt='Integração e cópia de segurança', nl='Integratie en back-up', pl='Integracja i kopia zapasowa'),
    'Backup and restore': dict(de='Sichern und wiederherstellen', fr='Sauvegarder et restaurer', es='Copia y restauración', it='Backup e ripristino', pt='Cópia e restauração', nl='Back-up en herstellen', pl='Kopia i przywracanie'),
    'Repair sync': dict(de='Abgleich reparieren', fr='Réparer la synchronisation', es='Reparar sincronización', it='Ripara sincronizzazione', pt='Reparar sincronização', nl='Synchronisatie herstellen', pl='Napraw synchronizację'),
    'Ready': dict(de='Bereit', fr='Prêt', es='Listo', it='Pronto', pt='Pronto', nl='Gereed', pl='Gotowe'),
    'Needs attention': dict(de='Prüfung nötig', fr='Attention requise', es='Requiere atención', it='Richiede attenzione', pt='Requer atenção', nl='Aandacht nodig', pl='Wymaga uwagi'),
    'Off': dict(de='Aus', fr='Désactivé', es='Desactivado', it='Disattivato', pt='Desligado', nl='Uit', pl='Wyłączone'),
    'Export…': dict(de='Exportieren…', fr='Exporter…', es='Exportar…', it='Esporta…', pt='Exportar…', nl='Exporteren…', pl='Eksportuj…'),
    'Restore…': dict(de='Wiederherstellen…', fr='Restaurer…', es='Restaurar…', it='Ripristina…', pt='Restaurar…', nl='Herstellen…', pl='Przywróć…'),
    'Restore': dict(de='Wiederherstellen', fr='Restaurer', es='Restaurar', it='Ripristina', pt='Restaurar', nl='Herstellen', pl='Przywróć'),
    'Save the current theme, wallpaper and font for later.': dict(de='Aktuelles Theme, Hintergrund und Schrift für später speichern.', fr='Enregistrer le thème, le fond d’écran et la police actuels.', es='Guarda el tema, el fondo y la fuente actuales.', it='Salva il tema, lo sfondo e il carattere attuali.', pt='Guarde o tema, o papel de parede e a fonte atuais.', nl='Sla het huidige thema, de achtergrond en het lettertype op.', pl='Zapisz bieżący motyw, tapetę i czcionkę.'),
    'Assign theme and/or wallpaper to workspace': dict(de='Theme und/oder Hintergrund einer Arbeitsbereich zuweisen', fr='Attribuer le thème et/ou le fond à un espace de travail', es='Asignar tema y/o fondo al espacio de trabajo', it='Assegna tema e/o sfondo allo spazio di lavoro', pt='Atribuir tema e/ou papel de parede à área de trabalho', nl='Thema en/of achtergrond aan werkblad toewijzen', pl='Przypisz motyw lub tapetę do obszaru roboczego'),
    'Use theme on workspace': dict(de='Theme auf Arbeitsbereich verwenden', fr='Utiliser le thème sur cet espace', es='Usar tema en este espacio', it='Usa il tema in questo spazio', pt='Usar tema nesta área', nl='Thema op werkblad gebruiken', pl='Użyj motywu w obszarze'),
    'Switches the global theme when that workspace gains focus': dict(de='Wechselt das globale Theme beim Fokus auf diese Arbeitsbereich', fr='Change le thème global quand cet espace est actif', es='Cambia el tema global al enfocar este espacio', it='Cambia il tema globale quando attivi questo spazio', pt='Muda o tema global ao focar esta área', nl='Wijzigt het globale thema bij focus op deze werkblad', pl='Zmienia globalny motyw po przejściu do tego obszaru'),
    "Switches Omarchy's global theme when you focus a workspace with an assigned theme.": dict(de='Wechselt Omarchys globales Theme beim Fokus auf eine Arbeitsbereich mit zugewiesenem Theme.', fr='Change le thème global d’Omarchy quand un espace attribué devient actif.', es='Cambia el tema global de Omarchy al enfocar un espacio asignado.', it='Cambia il tema globale di Omarchy quando attivi uno spazio assegnato.', pt='Muda o tema global do Omarchy ao focar uma área atribuída.', nl='Wijzigt het globale thema van Omarchy bij focus op een toegewezen werkblad.', pl='Zmienia globalny motyw Omarchy po wejściu do przypisanego obszaru.'),
    'Includes settings, lists, workspace assignments, presets and shortcuts; themes and wallpapers remain in their folders.': dict(de='Enthält Einstellungen, Listen, Arbeitsbereichszuweisungen, Voreinstellungen und Tastenkürzel; Themes und Bilder bleiben in ihren Ordnern.', fr='Inclut réglages, listes, espaces attribués, préréglages et raccourcis ; thèmes et fonds restent dans leurs dossiers.', es='Incluye ajustes, listas, espacios asignados, preajustes y atajos; temas y fondos quedan en sus carpetas.', it='Include impostazioni, elenchi, spazi assegnati, preimpostazioni e scorciatoie; temi e sfondi restano nelle loro cartelle.', pt='Inclui configurações, listas, áreas atribuídas, presets e atalhos; temas e imagens ficam nas suas pastas.', nl='Bevat instellingen, lijsten, werkbladen, voorinstellingen en sneltoetsen; thema’s en achtergronden blijven in hun mappen.', pl='Zawiera ustawienia, listy, przypisania obszarów, presety i skróty; motywy i tapety zostają w folderach.'),
    'Omarchy picker sync': dict(de='Abgleich mit Omarchy-Auswahl', fr='Synchronisation du sélecteur Omarchy', es='Sincronización del selector de Omarchy', it='Sincronizzazione selettore Omarchy', pt='Sincronização do seletor Omarchy', nl='Synchronisatie Omarchy-kiezer', pl='Synchronizacja wybieraka Omarchy'),
    'Delete preset “{0}”?': dict(de='Voreinstellung „{0}“ löschen?', fr='Supprimer le préréglage « {0} » ?', es='¿Eliminar el preajuste «{0}»?', it='Eliminare la preimpostazione «{0}»?', pt='Excluir a preset «{0}»?', nl='Voorinstelling ‘{0}’ verwijderen?', pl='Usunąć preset „{0}”?'),
    'Workspace {0} assigned': dict(de='Arbeitsbereich {0} zugewiesen', fr='Espace {0} attribué', es='Espacio {0} asignado', it='Spazio {0} assegnato', pt='Área {0} atribuída', nl='Werkblad {0} toegewezen', pl='Przypisano obszar {0}'),
    'Export Theme Manager backup': dict(de='Theme-Manager-Sicherung exportieren', fr='Exporter la sauvegarde Theme Manager', es='Exportar copia de Theme Manager', it='Esporta backup di Theme Manager', pt='Exportar cópia do Theme Manager', nl='Theme Manager-back-up exporteren', pl='Eksportuj kopię Theme Manager'),
    'Restore Theme Manager backup': dict(de='Theme-Manager-Sicherung wiederherstellen', fr='Restaurer la sauvegarde Theme Manager', es='Restaurar copia de Theme Manager', it='Ripristina backup di Theme Manager', pt='Restaurar cópia do Theme Manager', nl='Theme Manager-back-up herstellen', pl='Przywróć kopię Theme Manager'),
    'Could not save preset': dict(de='Voreinstellung konnte nicht gespeichert werden', fr='Impossible d’enregistrer le préréglage', es='No se pudo guardar el preajuste', it='Impossibile salvare la preimpostazione', pt='Não foi possível salvar a preset', nl='Voorinstelling kon niet worden opgeslagen', pl='Nie można zapisać presetu'),
    'Could not apply preset': dict(de='Voreinstellung konnte nicht angewendet werden', fr='Impossible d’appliquer le préréglage', es='No se pudo aplicar el preajuste', it='Impossibile applicare la preimpostazione', pt='Não foi possível aplicar a preset', nl='Voorinstelling kon niet worden toegepast', pl='Nie można zastosować presetu'),
    'Could not assign workspace': dict(de='Arbeitsbereich konnte nicht zugewiesen werden', fr='Impossible d’attribuer l’espace de travail', es='No se pudo asignar el espacio', it='Impossibile assegnare lo spazio', pt='Não foi possível atribuir a área', nl='Werkblad kon niet worden toegewezen', pl='Nie można przypisać obszaru'),
    'Preset applied': dict(de='Voreinstellung angewendet', fr='Préréglage appliqué', es='Preajuste aplicado', it='Preimpostazione applicata', pt='Preset aplicada', nl='Voorinstelling toegepast', pl='Zastosowano preset'),
    'Backup failed': dict(de='Sicherung fehlgeschlagen', fr='Échec de la sauvegarde', es='Error en la copia', it='Backup non riuscito', pt='Falha na cópia', nl='Back-up mislukt', pl='Kopia nie powiodła się'),
    'Backup invalid': dict(de='Ungültige Sicherung', fr='Sauvegarde invalide', es='Copia no válida', it='Backup non valido', pt='Cópia inválida', nl='Ongeldige back-up', pl='Nieprawidłowa kopia'),
    'Backup saved': dict(de='Sicherung gespeichert', fr='Sauvegarde enregistrée', es='Copia guardada', it='Backup salvato', pt='Cópia guardada', nl='Back-up opgeslagen', pl='Zapisano kopię'),
    'Backup restored': dict(de='Sicherung wiederhergestellt', fr='Sauvegarde restaurée', es='Copia restaurada', it='Backup ripristinato', pt='Cópia restaurada', nl='Back-up hersteld', pl='Przywrócono kopię'),
    'Restore failed': dict(de='Wiederherstellung fehlgeschlagen', fr='Échec de la restauration', es='Error al restaurar', it='Ripristino non riuscito', pt='Falha no restauração', nl='Herstellen mislukt', pl='Przywracanie nie powiodło się'),
    'Sync repair failed': dict(de='Abgleich konnte nicht repariert werden', fr='Échec de la réparation', es='No se pudo reparar la sincronización', it='Riparazione non riuscita', pt='Falha na reparação', nl='Synchronisatie herstellen mislukt', pl='Nie udało się naprawić synchronizacji'),
    'Sync repaired': dict(de='Abgleich repariert', fr='Synchronisation réparée', es='Sincronización reparada', it='Sincronizzazione riparata', pt='Sincronização reparada', nl='Synchronisatie hersteld', pl='Naprawiono synchronizację'),
}

NEW_KEYS = [
    'Apply the preset theme too, or choose a preset for the active theme.',
    'A theme for each workspace', 'Apply', 'Assign theme and/or wallpaper to workspace',
    'Assign to workspace', 'Backup and restore', 'Backup failed', 'Backup invalid',
    'Backup restored', 'Backup saved', 'Could not apply preset', 'Could not assign workspace',
    'Could not save preset', 'Delete preset', 'Delete preset “{0}”?',
    'Export Theme Manager backup', 'Export…',
    'Includes settings, lists, workspace assignments, presets and shortcuts; themes and wallpapers remain in their folders.',
    'Integration and backup', 'Needs attention', 'No presets yet.', 'Off',
    'Omarchy picker sync', 'Preset applied', 'Preset name', 'Presets', 'Ready',
    'Repair sync', 'Restore', 'Restore Theme Manager backup', 'Restore failed',
    'Restore…', 'Save current', 'Save the current theme, wallpaper and font for later.',
    "Switches Omarchy's global theme when you focus a workspace with an assigned theme.",
    'Switches the global theme when that workspace gains focus', 'Sync repair failed',
    'Sync repaired', 'Theme', 'Theme + wallpaper',
    'Use on workspace…', 'Use theme on workspace', 'Wallpaper', 'Workspace {0} assigned',
]


# Workspace Presets layouts and app presets inside a theme preset.
WORKSPACE_TRANSLATIONS = {
    'Workspace preview': dict(de='Arbeitsbereich-Vorschau', fr='Aperçu de l’espace', es='Vista previa del espacio', it='Anteprima dello spazio', pt='Pré-visualização da área', nl='Werkbladvoorbeeld', pl='Podgląd obszaru'),
    'Swap these two windows': dict(de='Diese zwei Fenster tauschen', fr='Échanger ces deux fenêtres', es='Intercambiar estas dos ventanas', it='Scambia queste due finestre', pt='Trocar estas duas janelas', nl='Deze twee vensters wisselen', pl='Zamień te dwa okna'),
    'Undone.': dict(de='Rückgängig gemacht.', fr='Annulé.', es='Deshecho.', it='Annullato.', pt='Anulado.', nl='Ongedaan gemaakt.', pl='Cofnięto.'),
    'Drag a window onto another to swap them, or onto a layout in the row that appears at the bottom · the master window over the others moves it to that side · out of the preview for one window fewer · the gap at the right edge sets the window limit. The line between the windows sets their sizes (double-click: even). Keys: ←/→, Shift+←/→ swap, ↑/↓ layout, [ and ] size, + and −.': dict(
        de='Ein Fenster auf ein anderes ziehen zum Tauschen, oder auf ein Layout in der Leiste, die unten erscheint · das Master-Fenster über die anderen legt es auf diese Seite · aus der Vorschau heraus für ein Fenster weniger · die Lücke am rechten Rand stellt die Fenstergrenze ein. Die Linie zwischen den Fenstern stellt ihre Größen ein (Doppelklick: gleich). Tasten: ←/→, Umschalt+←/→ tauschen, ↑/↓ Layout, [ und ] Größe, + und −.',
        fr='Glissez une fenêtre sur une autre pour les échanger, ou sur une disposition de la rangée qui apparaît en bas · la fenêtre maître sur les autres la place de ce côté · hors de l’aperçu pour une fenêtre de moins · l’espace au bord droit règle la limite. La ligne entre les fenêtres règle leurs tailles (double-clic : égales). Touches : ←/→, Maj+←/→ échanger, ↑/↓ disposition, [ et ] taille, + et −.',
        es='Arrastra una ventana sobre otra para intercambiarlas, o sobre una disposición de la fila que aparece abajo · la ventana maestra sobre las otras la mueve a ese lado · fuera de la vista para una ventana menos · el hueco del borde derecho fija el límite. La línea entre las ventanas fija sus tamaños (doble clic: iguales). Teclas: ←/→, Mayús+←/→ intercambiar, ↑/↓ disposición, [ y ] tamaño, + y −.',
        it='Trascina una finestra su un’altra per scambiarle, o su un layout nella fila che appare in basso · la finestra master sulle altre la sposta su quel lato · fuori dall’anteprima per una finestra in meno · lo spazio al bordo destro imposta il limite. La linea tra le finestre ne imposta le dimensioni (doppio clic: uguali). Tasti: ←/→, Maiusc+←/→ scambia, ↑/↓ layout, [ e ] dimensione, + e −.',
        pt='Arraste uma janela para outra para as trocar, ou para um layout na fila que aparece em baixo · a janela mestre sobre as outras move-a para esse lado · para fora da pré-visualização para uma janela a menos · o espaço na margem direita define o limite. A linha entre as janelas define os seus tamanhos (duplo clique: iguais). Teclas: ←/→, Shift+←/→ trocar, ↑/↓ layout, [ e ] tamanho, + e −.',
        nl='Sleep een venster op een ander om ze te wisselen, of op een indeling in de rij die onderaan verschijnt · het mastervenster over de andere verplaatst het naar die kant · uit het voorbeeld voor één venster minder · de ruimte aan de rechterrand stelt de vensterlimiet in. De lijn tussen de vensters stelt hun grootte in (dubbelklik: gelijk). Toetsen: ←/→, Shift+←/→ wisselen, ↑/↓ indeling, [ en ] grootte, + en −.',
        pl='Przeciągnij okno na inne, aby je zamienić, lub na układ w rzędzie, który pojawia się na dole · okno główne na pozostałe przenosi je na tę stronę · poza podgląd, aby mieć jedno okno mniej · odstęp przy prawej krawędzi ustawia limit. Linia między oknami ustawia ich rozmiary (dwuklik: równe). Klawisze: ←/→, Shift+←/→ zamiana, ↑/↓ układ, [ i ] rozmiar, + i −.'),
    'Split {0} % · {1} %': dict(
        de='Aufteilung {0} % · {1} %',
        fr='Partage {0} % · {1} %',
        es='División {0} % · {1} %',
        it='Divisione {0} % · {1} %',
        pt='Divisão {0} % · {1} %',
        nl='Verdeling {0} % · {1} %',
        pl='Podział {0} % · {1} %'),
    'Even split': dict(
        de='Gleich aufgeteilt',
        fr='Partage égal',
        es='División igual',
        it='Divisione uguale',
        pt='Divisão igual',
        nl='Gelijke verdeling',
        pl='Równy podział'),
    '− 1 window': dict(
        de='− 1 Fenster',
        fr='− 1 fenêtre',
        es='− 1 ventana',
        it='− 1 finestra',
        pt='− 1 janela',
        nl='− 1 venster',
        pl='− 1 okno'),
    'Already {0}': dict(
        de='Schon {0}',
        fr='Déjà {0}',
        es='Ya es {0}',
        it='Già {0}',
        pt='Já é {0}',
        nl='Al {0}',
        pl='Już {0}'),
    'Drag a window onto another to swap them · drag the right edge for the window limit · drop a layout here to use it': dict(de='Ein Fenster auf ein anderes ziehen, um sie zu tauschen · den rechten Rand ziehen für die Fenstergrenze · ein Layout hier ablegen, um es zu verwenden', fr='Glissez une fenêtre sur une autre pour les échanger · faites glisser le bord droit pour la limite de fenêtres · déposez une disposition ici pour l’utiliser', es='Arrastra una ventana sobre otra para intercambiarlas · arrastra el borde derecho para el límite de ventanas · suelta aquí una disposición para usarla', it='Trascina una finestra su un’altra per scambiarle · trascina il bordo destro per il limite di finestre · rilascia qui un layout per usarlo', pt='Arraste uma janela para outra para as trocar · arraste a margem direita para o limite de janelas · largue aqui um layout para o usar', nl='Sleep een venster op een ander om ze te wisselen · sleep de rechterrand voor de vensterlimiet · zet hier een indeling neer om die te gebruiken', pl='Przeciągnij okno na inne, aby je zamienić · przeciągnij prawą krawędź, aby zmienić limit okien · upuść tu układ, aby go użyć'),
    'Window limit: {0}': dict(de='Fenstergrenze: {0}', fr='Limite de fenêtres : {0}', es='Límite de ventanas: {0}', it='Limite di finestre: {0}', pt='Limite de janelas: {0}', nl='Vensterlimiet: {0}', pl='Limit okien: {0}'),
    'Swapped two windows of the App Preset.': dict(de='Zwei Fenster des App-Presets getauscht.', fr='Deux fenêtres du préréglage d’applications échangées.', es='Se intercambiaron dos ventanas del preajuste de aplicaciones.', it='Scambiate due finestre della preimpostazione app.', pt='Trocadas duas janelas do preset de apps.', nl='Twee vensters van de app-voorinstelling gewisseld.', pl='Zamieniono dwa okna presetu aplikacji.'),
    'Swapped two windows on Workspace {0}.': dict(de='Zwei Fenster auf Arbeitsbereich {0} getauscht.', fr='Deux fenêtres échangées sur l’espace {0}.', es='Se intercambiaron dos ventanas en el espacio {0}.', it='Scambiate due finestre nello spazio {0}.', pt='Trocadas duas janelas na área {0}.', nl='Twee vensters op werkblad {0} gewisseld.', pl='Zamieniono dwa okna na obszarze {0}.'),
    'Workspace {0} has no two open windows there to swap.': dict(de='Auf Arbeitsbereich {0} sind dort keine zwei offenen Fenster zum Tauschen.', fr='L’espace {0} n’a pas là deux fenêtres ouvertes à échanger.', es='El espacio {0} no tiene ahí dos ventanas abiertas para intercambiar.', it='Lo spazio {0} non ha lì due finestre aperte da scambiare.', pt='A área {0} não tem ali duas janelas abertas para trocar.', nl='Werkblad {0} heeft daar geen twee open vensters om te wisselen.', pl='Obszar {0} nie ma tam dwóch otwartych okien do zamiany.'),
    'Workspace {0} still floats or scrolls.': dict(de='Arbeitsbereich {0} ist noch frei schwebend oder scrollend.', fr='L’espace {0} flotte ou défile encore.', es='El espacio {0} sigue flotante o con desplazamiento.', it='Lo spazio {0} è ancora flottante o a scorrimento.', pt='A área {0} ainda flutua ou rola.', nl='Werkblad {0} zweeft of scrolt nog.', pl='Obszar {0} nadal jest pływający lub przewijany.'),
    'not in use': dict(de='nicht in Gebrauch', fr='inutilisé', es='sin usar', it='non in uso', pt='sem uso', nl='niet in gebruik', pl='nieużywany'),
    'The presets are too large to save: delete a preset with workspace presets first.': dict(de='Die Voreinstellungen sind zu groß zum Speichern: Lösche zuerst eine Voreinstellung mit Arbeitsbereich-Presets.', fr='Les préréglages sont trop volumineux : supprimez d’abord un préréglage contenant des préréglages d’espaces.', es='Los preajustes son demasiado grandes: elimina antes un preajuste con preajustes de espacios.', it='Le preimpostazioni sono troppo grandi: elimina prima una preimpostazione con preimpostazioni degli spazi.', pt='Os presets são grandes demais: apague primeiro um preset com presets de áreas.', nl='De voorinstellingen zijn te groot om op te slaan: verwijder eerst een voorinstelling met werkbladvoorinstellingen.', pl='Presety są za duże do zapisania: najpierw usuń preset z presetami obszarów.'),
    'Use App Preset': dict(de='App-Preset verwenden', fr='Utiliser le préréglage d’applications', es='Usar el preajuste de aplicaciones', it='Usa la preimpostazione app', pt='Usar o preset de apps', nl='App-voorinstelling gebruiken', pl='Użyj presetu aplikacji'),
    'Saved, not in use: the workspace tiles with its layout and nothing starts at login.': dict(de='Gespeichert, nicht in Gebrauch: Der Arbeitsbereich kachelt mit seinem Layout, und beim Anmelden startet nichts.', fr='Enregistré, inutilisé : l’espace suit sa disposition et rien ne démarre à la connexion.', es='Guardado, sin usar: el espacio usa su disposición y nada se abre al iniciar sesión.', it='Salvata, non in uso: lo spazio segue il suo layout e all’accesso non parte nulla.', pt='Guardado, sem uso: a área segue o seu layout e nada abre ao iniciar sessão.', nl='Opgeslagen, niet in gebruik: het werkblad volgt zijn indeling en bij het aanmelden start er niets.', pl='Zapisany, nieużywany: obszar używa swojego układu i nic nie uruchamia się przy logowaniu.'),
    'Workspaces {0}–{1}': dict(de='Arbeitsbereiche {0}–{1}', fr='Espaces {0}–{1}', es='Espacios {0}–{1}', it='Spazi {0}–{1}', pt='Áreas {0}–{1}', nl='Werkbladen {0}–{1}', pl='Obszary {0}–{1}'),
    'Preview': dict(de='Vorschau', fr='Aperçu', es='Vista previa', it='Anteprima', pt='Pré-visualização', nl='Voorbeeld', pl='Podgląd'),
    'Save/Load Presets': dict(de='Presets speichern/laden', fr='Enregistrer/charger des préréglages', es='Guardar/cargar preajustes', it='Salva/carica preimpostazioni', pt='Guardar/carregar presets', nl='Voorinstellingen opslaan/laden', pl='Zapisz/wczytaj presety'),
    'Screenshot saved: {0}': dict(de='Screenshot gespeichert: {0}', fr='Capture enregistrée : {0}', es='Captura guardada: {0}', it='Screenshot salvato: {0}', pt='Captura guardada: {0}', nl='Schermafbeelding opgeslagen: {0}', pl='Zapisano zrzut ekranu: {0}'),
    'What is wrong? (optional)': dict(de='Was stimmt nicht? (optional)', fr='Qu’est-ce qui ne va pas ? (facultatif)', es='¿Qué falla? (opcional)', it='Cosa non va? (facoltativo)', pt='O que está errado? (opcional)', nl='Wat klopt er niet? (optioneel)', pl='Co jest nie tak? (opcjonalnie)'),
    'Include workspace presets and app presets': dict(de='Arbeitsbereich-Presets und App-Presets einschließen', fr='Inclure les préréglages d’espaces et d’applications', es='Incluir preajustes de espacios y de aplicaciones', it='Includi preimpostazioni degli spazi e delle app', pt='Incluir presets de áreas de trabalho e de apps', nl='Werkblad- en app-voorinstellingen meenemen', pl='Dołącz presety obszarów roboczych i aplikacji'),
    'Workspace presets: {0} · App presets: {1}': dict(de='Arbeitsbereich-Presets: {0} · App-Presets: {1}', fr='Préréglages d’espaces : {0} · d’applications : {1}', es='Preajustes de espacios: {0} · de aplicaciones: {1}', it='Preimpostazioni degli spazi: {0} · delle app: {1}', pt='Presets de áreas: {0} · de apps: {1}', nl='Werkbladvoorinstellingen: {0} · app-voorinstellingen: {1}', pl='Presety obszarów: {0} · aplikacji: {1}'),
    'Apply “{0}”?': dict(de='„{0}“ anwenden?', fr='Appliquer « {0} » ?', es='¿Aplicar «{0}»?', it='Applicare «{0}»?', pt='Aplicar “{0}”?', nl='“{0}” toepassen?', pl='Zastosować „{0}”?'),
    'Missing apps on workspaces {0} start; other windows there move to a free workspace.': dict(de='Fehlende Apps auf den Arbeitsbereichen {0} starten; andere Fenster dort ziehen auf einen freien Arbeitsbereich um.', fr='Les applications manquantes des espaces {0} démarrent ; les autres fenêtres y passent sur un espace libre.', es='Se abren las aplicaciones que faltan en los espacios {0}; las demás ventanas pasan a un espacio libre.', it='Le app mancanti negli spazi {0} si avviano; le altre finestre lì passano a uno spazio libero.', pt='Os apps em falta nas áreas {0} abrem; as outras janelas ali vão para uma área livre.', nl='Ontbrekende apps op werkbladen {0} starten; andere vensters daar gaan naar een vrij werkblad.', pl='Brakujące aplikacje na obszarach {0} uruchomią się; inne okna z tych obszarów trafią na wolny obszar.'),
    'Some workspace apps did not start.': dict(de='Einige Apps der Arbeitsbereiche sind nicht gestartet.', fr='Certaines applications des espaces n’ont pas démarré.', es='Algunas aplicaciones de los espacios no se abrieron.', it='Alcune app degli spazi non si sono avviate.', pt='Alguns apps das áreas não abriram.', nl='Sommige apps van de werkbladen zijn niet gestart.', pl='Niektóre aplikacje obszarów nie uruchomiły się.'),
}

# Ask AI page and setting.
ASK_TRANSLATIONS = {
    'Maprisma hints': dict(de='Maprisma-Hinweise', fr='Conseils Maprisma', es='Avisos de Maprisma', it='Suggerimenti Maprisma', pt='Dicas do Maprisma', nl='Maprisma-tips', pl='Wskazówki Maprisma'),
    'Find or make a theme from a few words with a coding agent on this computer (Claude Code, Codex, pi or Hermes) or a local model, using your plan or tokens only when you click Ask; a local model uses none and nothing leaves the computer. Each Ask sends your words with the names and main colors of your installed themes, the omarchy.org theme names and your installed font names; nothing else.': dict(de='Finde oder erstelle ein Theme aus wenigen Worten mit einem Coding-Agenten auf diesem Computer (Claude Code, Codex, pi oder Hermes) oder einem lokalen Modell; dein Plan oder deine Tokens werden nur beim Klick auf Fragen genutzt, ein lokales Modell braucht keine und nichts verlässt den Computer. Jede Frage sendet deine Worte mit den Namen und Hauptfarben deiner installierten Themes, den Theme-Namen von omarchy.org und den Namen deiner installierten Schriften; sonst nichts.', fr='Trouvez ou créez un thème en quelques mots avec un agent de code sur cet ordinateur (Claude Code, Codex, pi ou Hermes) ou un modèle local ; votre forfait ou vos jetons ne servent que lorsque vous cliquez sur Demander, un modèle local n’en utilise aucun et rien ne quitte l’ordinateur. Chaque demande envoie vos mots avec les noms et couleurs principales de vos thèmes installés, les noms des thèmes d’omarchy.org et les noms de vos polices installées ; rien d’autre.', es='Encuentra o crea un tema con unas pocas palabras con un agente de código en este equipo (Claude Code, Codex, pi o Hermes) o un modelo local; tu plan o tus tokens solo se usan al pulsar Preguntar, un modelo local no usa ninguno y nada sale del equipo. Cada pregunta envía tus palabras con los nombres y colores principales de tus temas instalados, los nombres de temas de omarchy.org y los nombres de tus fuentes instaladas; nada más.', it='Trova o crea un tema con poche parole con un agente di programmazione su questo computer (Claude Code, Codex, pi o Hermes) o un modello locale; il tuo piano o i token si usano solo quando fai clic su Chiedi, un modello locale non ne usa e nulla lascia il computer. Ogni domanda invia le tue parole con i nomi e i colori principali dei temi installati, i nomi dei temi di omarchy.org e i nomi dei font installati; nient’altro.', pt='Encontre ou crie um tema com poucas palavras com um agente de programação neste computador (Claude Code, Codex, pi ou Hermes) ou um modelo local; o seu plano ou tokens só são usados quando clica em Perguntar, um modelo local não usa nenhum e nada sai do computador. Cada pergunta envia as suas palavras com os nomes e cores principais dos temas instalados, os nomes dos temas de omarchy.org e os nomes das fontes instaladas; nada mais.', nl="Vind of maak een thema met een paar woorden met een code-agent op deze computer (Claude Code, Codex, pi of Hermes) of een lokaal model; je abonnement of tokens worden alleen gebruikt als je op Vragen klikt, een lokaal model gebruikt er geen en niets verlaat de computer. Elke vraag stuurt je woorden mee met de namen en hoofdkleuren van je geïnstalleerde thema's, de themanamen van omarchy.org en de namen van je geïnstalleerde lettertypen; verder niets.", pl='Znajdź lub utwórz motyw z kilku słów za pomocą agenta programistycznego na tym komputerze (Claude Code, Codex, pi lub Hermes) albo modelu lokalnego; Twój plan lub tokeny są używane tylko po kliknięciu Zapytaj, model lokalny nie zużywa żadnych i nic nie opuszcza komputera. Każde pytanie wysyła Twoje słowa z nazwami i głównymi kolorami zainstalowanych motywów, nazwami motywów z omarchy.org i nazwami zainstalowanych czcionek; nic więcej.'),
    'No helper ready: install Claude Code, Codex, pi or Hermes and sign in, or connect pi or Hermes to a local model.': dict(de='Kein Helfer bereit: Claude Code, Codex, pi oder Hermes installieren und anmelden, oder pi bzw. Hermes mit einem lokalen Modell verbinden.', fr='Aucun assistant prêt : installez Claude Code, Codex, pi ou Hermes et connectez-vous, ou reliez pi ou Hermes à un modèle local.', es='Ningún ayudante listo: instala Claude Code, Codex, pi o Hermes e inicia sesión, o conecta pi o Hermes a un modelo local.', it='Nessun assistente pronto: installa Claude Code, Codex, pi o Hermes e accedi, oppure collega pi o Hermes a un modello locale.', pt='Nenhum assistente pronto: instale Claude Code, Codex, pi ou Hermes e inicie sessão, ou ligue pi ou Hermes a um modelo local.', nl='Geen helper klaar: installeer Claude Code, Codex, pi of Hermes en meld je aan, of koppel pi of Hermes aan een lokaal model.', pl='Brak gotowego pomocnika: zainstaluj Claude Code, Codex, pi lub Hermes i zaloguj się albo połącz pi lub Hermes z modelem lokalnym.'),
    'Maprisma sets up AI on Omarchy for you: online agents and their providers, and local models with llama.cpp or Ollama, connected on one map. Every agent it sets up can answer here too.': dict(de='Maprisma richtet KI auf Omarchy für dich ein: Online-Agenten und ihre Anbieter sowie lokale Modelle mit llama.cpp oder Ollama, verbunden auf einer Karte. Jeder Agent, den es einrichtet, kann auch hier antworten.', fr='Maprisma configure l’IA sur Omarchy pour vous : des agents en ligne et leurs fournisseurs, et des modèles locaux avec llama.cpp ou Ollama, reliés sur une seule carte. Chaque agent qu’il configure peut aussi répondre ici.', es='Maprisma configura la IA en Omarchy por ti: agentes en línea y sus proveedores, y modelos locales con llama.cpp u Ollama, conectados en un solo mapa. Cada agente que configura también puede responder aquí.', it='Maprisma configura l’IA su Omarchy per te: agenti online e i loro fornitori, e modelli locali con llama.cpp o Ollama, collegati su un’unica mappa. Ogni agente che configura può rispondere anche qui.', pt='O Maprisma configura a IA no Omarchy por si: agentes online e os seus fornecedores, e modelos locais com llama.cpp ou Ollama, ligados num só mapa. Cada agente que configura também pode responder aqui.', nl='Maprisma richt AI op Omarchy voor je in: online agents en hun aanbieders, en lokale modellen met llama.cpp of Ollama, verbonden op één kaart. Elke agent die het instelt, kan hier ook antwoorden.', pl='Maprisma konfiguruje dla Ciebie AI w Omarchy: agentów online i ich dostawców oraz modele lokalne z llama.cpp lub Ollama, połączone na jednej mapie. Każdy skonfigurowany przez niego agent może też odpowiadać tutaj.'),
    'Easy AI setup with Maprisma': dict(de='Einfache KI-Einrichtung mit Maprisma', fr='Configuration de l’IA facile avec Maprisma', es='Configuración sencilla de IA con Maprisma', it='Configurazione IA semplice con Maprisma', pt='Configuração de IA fácil com o Maprisma', nl='Eenvoudige AI-installatie met Maprisma', pl='Łatwa konfiguracja AI z Maprisma'),
    'Get Maprisma in the Omarchy Plugin Marketplace': dict(de='Maprisma im Omarchy Plugin Marketplace holen', fr='Obtenir Maprisma sur l’Omarchy Plugin Marketplace', es='Obtener Maprisma en el Omarchy Plugin Marketplace', it='Scarica Maprisma dall’Omarchy Plugin Marketplace', pt='Obter o Maprisma no Omarchy Plugin Marketplace', nl='Maprisma halen in de Omarchy Plugin Marketplace', pl='Pobierz Maprisma z Omarchy Plugin Marketplace'),
    'on this PC': dict(de='auf diesem PC', fr='sur ce PC', es='en este PC', it='su questo PC', pt='neste PC', nl='op deze pc', pl='na tym komputerze'),
    'On this PC: no tokens, nothing leaves it. Enter asks.': dict(de='Auf diesem PC: keine Tokens, nichts verlässt ihn. Enter fragt.', fr='Sur ce PC : aucun jeton, rien n’en sort. Entrée demande.', es='En este PC: sin tokens, nada sale de él. Intro pregunta.', it='Su questo PC: nessun token, nulla esce. Invio chiede.', pt='Neste PC: sem tokens, nada sai dele. Enter pergunta.', nl='Op deze pc: geen tokens, er gaat niets naar buiten. Enter vraagt.', pl='Na tym komputerze: bez tokenów, nic go nie opuszcza. Enter pyta.'),
    'Wallpaper…': dict(de='Hintergrund…', fr='Fond d’écran…', es='Fondo…', it='Sfondo…', pt='Papel de parede…', nl='Achtergrond…', pl='Tapeta…'),
    'Search wallpapers, e.g. “husky snow”': dict(de='Hintergründe suchen, z. B. „Husky Schnee“', fr='Chercher des fonds, p. ex. « husky neige »', es='Buscar fondos, p. ej. «husky nieve»', it='Cerca sfondi, es. «husky neve»', pt='Procurar papéis de parede, ex.: “husky neve”', nl='Achtergronden zoeken, bijv. “husky sneeuw”', pl='Szukaj tapet, np. „husky śnieg”'),
    'Search wallpapers (no tokens)': dict(de='Hintergründe suchen (keine Tokens)', fr='Chercher des fonds (sans jetons)', es='Buscar fondos (sin tokens)', it='Cerca sfondi (nessun token)', pt='Procurar papéis de parede (sem tokens)', nl='Achtergronden zoeken (geen tokens)', pl='Szukaj tapet (bez tokenów)'),
    'Could not download the wallpaper': dict(de='Hintergrund konnte nicht geladen werden', fr='Impossible de télécharger le fond', es='No se pudo descargar el fondo', it='Impossibile scaricare lo sfondo', pt='Não foi possível transferir o papel de parede', nl='De achtergrond kon niet worden gedownload', pl='Nie udało się pobrać tapety'),
    'Asking {0}… {1} s': dict(de='Frage {0}… {1} s', fr='Question à {0}… {1} s', es='Preguntando a {0}… {1} s', it='Chiedo a {0}… {1} s', pt='A perguntar a {0}… {1} s', nl='{0} wordt gevraagd… {1} s', pl='Pytam {0}… {1} s'),
    'Turn Ask AI on': dict(de='KI fragen einschalten', fr='Activer Demander à l’IA', es='Activar Preguntar a la IA', it='Attiva Chiedi all’IA', pt='Ativar Perguntar à IA', nl='AI vragen inschakelen', pl='Włącz Zapytaj AI'),
    'Ask AI (set up a helper first)': dict(de='KI fragen (zuerst einen Helfer einrichten)', fr='Demander à l’IA (configurez d’abord un assistant)', es='Preguntar a la IA (configura antes un asistente)', it='Chiedi all’IA (prima configura un assistente)', pt='Perguntar à IA (configure primeiro um assistente)', nl='AI vragen (eerst een helper instellen)', pl='Zapytaj AI (najpierw skonfiguruj pomocnika)'),
    'Ask AI (off: turn it on in Settings)': dict(de='KI fragen (aus: in den Einstellungen einschalten)', fr='Demander à l’IA (désactivé : activez-le dans les réglages)', es='Preguntar a la IA (desactivado: actívalo en Ajustes)', it='Chiedi all’IA (disattivato: attivalo nelle impostazioni)', pt='Perguntar à IA (desligado: ative nas definições)', nl='AI vragen (uit: schakel het in bij Instellingen)', pl='Zapytaj AI (wyłączone: włącz w ustawieniach)'),
    'A calm light theme for daytime': dict(de='Ein ruhiges helles Theme für den Tag', fr='Un thème clair et calme pour la journée', es='Un tema claro y tranquilo para el día', it='Un tema chiaro e tranquillo per il giorno', pt='Um tema claro e calmo para o dia', nl='Een rustig licht thema voor overdag', pl='Spokojny jasny motyw na dzień'),
    'Another font for the current theme': dict(de='Eine andere Schrift für das aktuelle Theme', fr='Une autre police pour le thème actuel', es='Otra fuente para el tema actual', it='Un altro carattere per il tema attuale', pt='Outra fonte para o tema atual', nl='Een ander lettertype voor het huidige thema', pl='Inna czcionka dla bieżącego motywu'),
    'Ask': dict(de='Fragen', fr='Demander', es='Preguntar', it='Chiedi', pt='Perguntar', nl='Vragen', pl='Zapytaj'),
    'Ask AI': dict(de='KI fragen', fr='Demander à l’IA', es='Preguntar a la IA', it='Chiedi all’IA', pt='Perguntar à IA', nl='AI vragen', pl='Zapytaj AI'),
    'Ask AI: find or make a theme': dict(de='KI fragen: ein Theme finden oder machen', fr='Demander à l’IA : trouver ou créer un thème', es='Preguntar a la IA: encontrar o crear un tema', it='Chiedi all’IA: trova o crea un tema', pt='Perguntar à IA: encontrar ou criar um tema', nl='AI vragen: een thema vinden of maken', pl='Zapytaj AI: znajdź lub stwórz motyw'),
    'Asking {0}… (up to four minutes)': dict(de='Frage {0}… (bis zu vier Minuten)', fr='Question à {0}… (jusqu’à quatre minutes)', es='Preguntando a {0}… (hasta cuatro minutos)', it='Chiedo a {0}… (fino a quattro minuti)', pt='A perguntar a {0}… (até quatro minutos)', nl='{0} wordt gevraagd… (tot vier minuten)', pl='Pytam {0}… (do czterech minut)'),
    'Asks once; uses your plan or tokens only now.': dict(de='Fragt einmal; nutzt deinen Plan oder Tokens nur jetzt.', fr='Demande une fois ; utilise votre forfait ou vos jetons seulement maintenant.', es='Pregunta una vez; usa tu plan o tokens solo ahora.', it='Chiede una volta; usa il tuo piano o i token solo ora.', pt='Pergunta uma vez; usa o seu plano ou tokens só agora.', nl='Vraagt één keer; gebruikt je abonnement of tokens alleen nu.', pl='Pyta raz; zużywa Twój plan lub tokeny tylko teraz.'),
    'Blue borders, red text and a nice dog wallpaper': dict(de='Blaue Rahmen, roter Text und ein schöner Hunde-Hintergrund', fr='Bordures bleues, texte rouge et un joli fond avec un chien', es='Bordes azules, texto rojo y un bonito fondo con un perro', it='Bordi blu, testo rosso e un bello sfondo con un cane', pt='Bordas azuis, texto vermelho e um bonito papel de parede com um cão', nl='Blauwe randen, rode tekst en een mooie hondenachtergrond', pl='Niebieskie ramki, czerwony tekst i ładna tapeta z psem'),
    'Find a warm dark theme in the catalog': dict(de='Ein warmes dunkles Theme im Katalog finden', fr='Trouver un thème sombre et chaud dans le catalogue', es='Encontrar un tema oscuro y cálido en el catálogo', it='Trova un tema scuro e caldo nel catalogo', pt='Encontrar um tema escuro e quente no catálogo', nl='Een warm donker thema in de catalogus vinden', pl='Znajdź ciepły ciemny motyw w katalogu'),
    'From omarchy.org': dict(de='Von omarchy.org', fr='Depuis omarchy.org', es='De omarchy.org', it='Da omarchy.org', pt='De omarchy.org', nl='Van omarchy.org', pl='Z omarchy.org'),
    'Helper:': dict(de='Helfer:', fr='Assistant :', es='Asistente:', it='Assistente:', pt='Assistente:', nl='Helper:', pl='Pomocnik:'),
    'Helpers: {0}': dict(de='Helfer: {0}', fr='Assistants : {0}', es='Asistentes: {0}', it='Assistenti: {0}', pt='Assistentes: {0}', nl='Helpers: {0}', pl='Pomocnicy: {0}'),
    'How should it change? e.g. “warmer”, “another picture like {0}”': dict(de='Wie soll es sich ändern? z. B. „wärmer“, „ein anderes Bild wie {0}“', fr='Que changer ? p. ex. « plus chaud », « une autre image comme {0} »', es='¿Qué cambiar? p. ej. «más cálido», «otra imagen como {0}»', it='Cosa cambiare? es. «più caldo», «un’altra immagine come {0}»', pt='O que mudar? ex.: “mais quente”, “outra imagem como {0}”', nl='Wat moet er veranderen? bijv. “warmer”, “een andere afbeelding zoals {0}”', pl='Co zmienić? np. „cieplej”, „inny obraz jak {0}”'),
    'How should it change? e.g. “warmer”, “another wallpaper”': dict(de='Wie soll es sich ändern? z. B. „wärmer“, „ein anderer Hintergrund“', fr='Que changer ? p. ex. « plus chaud », « un autre fond d’écran »', es='¿Qué cambiar? p. ej. «más cálido», «otro fondo»', it='Cosa cambiare? es. «più caldo», «un altro sfondo»', pt='O que mudar? ex.: “mais quente”, “outro papel de parede”', nl='Wat moet er veranderen? bijv. “warmer”, “een andere achtergrond”', pl='Co zmienić? np. „cieplej”, „inna tapeta”'),
    'Wallpaper: your search “{0}”': dict(de='Hintergrund: deine Suche „{0}“', fr='Fond d’écran : votre recherche « {0} »', es='Fondo: tu búsqueda «{0}»', it='Sfondo: la tua ricerca «{0}»', pt='Papel de parede: a sua pesquisa “{0}”', nl='Achtergrond: je zoekopdracht “{0}”', pl='Tapeta: Twoje wyszukiwanie „{0}”'),
    'No answer: {0}': dict(de='Keine Antwort: {0}', fr='Pas de réponse : {0}', es='Sin respuesta: {0}', it='Nessuna risposta: {0}', pt='Sem resposta: {0}', nl='Geen antwoord: {0}', pl='Brak odpowiedzi: {0}'),
    'No wallpapers found for “{0}”.': dict(de='Keine Hintergründe für „{0}“ gefunden.', fr='Aucun fond trouvé pour « {0} ».', es='No se encontraron fondos para «{0}».', it='Nessuno sfondo trovato per «{0}».', pt='Nenhum papel de parede encontrado para “{0}”.', nl='Geen achtergronden gevonden voor “{0}”.', pl='Nie znaleziono tapet dla „{0}”.'),
    'Not this': dict(de='Das nicht', fr='Pas celui-ci', es='Este no', it='Non questo', pt='Este não', nl='Niet deze', pl='Nie ten'),
    'Nothing usable came back. Try other words.': dict(de='Nichts Brauchbares kam zurück. Versuche andere Worte.', fr='Rien d’utilisable n’est revenu. Essayez d’autres mots.', es='No llegó nada utilizable. Prueba con otras palabras.', it='Non è tornato nulla di utilizzabile. Prova altre parole.', pt='Não veio nada utilizável. Tente outras palavras.', nl='Er kwam niets bruikbaars terug. Probeer andere woorden.', pl='Nie wróciło nic użytecznego. Spróbuj innych słów.'),
    'Refine…': dict(de='Verfeinern…', fr='Affiner…', es='Afinar…', it='Affina…', pt='Refinar…', nl='Verfijnen…', pl='Dopracuj…'),
    'Searching wallpapers for “{0}”…': dict(de='Suche Hintergründe für „{0}“…', fr='Recherche de fonds pour « {0} »…', es='Buscando fondos para «{0}»…', it='Cerco sfondi per «{0}»…', pt='A procurar papéis de parede para “{0}”…', nl='Achtergronden zoeken voor “{0}”…', pl='Szukam tapet dla „{0}”…'),
    'Use': dict(de='Verwenden', fr='Utiliser', es='Usar', it='Usa', pt='Usar', nl='Gebruiken', pl='Użyj'),
    'Uses your {0} plan or tokens, only when you click Ask. Enter asks.': dict(de='Nutzt deinen {0}-Plan oder Tokens nur, wenn du auf Fragen klickst. Enter fragt.', fr='Utilise votre forfait ou vos jetons {0}, seulement quand vous cliquez sur Demander. Entrée demande.', es='Usa tu plan o tokens de {0}, solo cuando pulsas Preguntar. Intro pregunta.', it='Usa il tuo piano o i token {0}, solo quando fai clic su Chiedi. Invio chiede.', pt='Usa o seu plano ou tokens {0}, só quando clica em Perguntar. Enter pergunta.', nl='Gebruikt je {0}-abonnement of tokens, alleen als je op Vragen klikt. Enter vraagt.', pl='Zużywa Twój plan lub tokeny {0} tylko po kliknięciu Zapytaj. Enter pyta.'),
    "Wallpaper: after saving, search “{0}” on the theme's wallpaper page.": dict(de='Hintergrund: Nach dem Speichern auf der Hintergrundseite des Themes nach „{0}“ suchen.', fr='Fond : après l’enregistrement, recherchez « {0} » sur la page des fonds du thème.', es='Fondo: después de guardar, busca «{0}» en la página de fondos del tema.', it='Sfondo: dopo il salvataggio, cerca «{0}» nella pagina degli sfondi del tema.', pt='Papel de parede: depois de guardar, procure “{0}” na página de papéis de parede do tema.', nl='Achtergrond: zoek na het opslaan “{0}” op de achtergrondpagina van het thema.', pl='Tapeta: po zapisaniu wyszukaj „{0}” na stronie tapet motywu.'),
    'What do you want? e.g. “blue borders, red text and a dog wallpaper”': dict(de='Was möchtest du? z. B. „blaue Rahmen, roter Text und ein Hunde-Hintergrund“', fr='Que voulez-vous ? p. ex. « bordures bleues, texte rouge et un fond avec un chien »', es='¿Qué quieres? p. ej. «bordes azules, texto rojo y un fondo con un perro»', it='Cosa vuoi? es. «bordi blu, testo rosso e uno sfondo con un cane»', pt='O que quer? ex.: “bordas azuis, texto vermelho e um papel de parede com um cão”', nl='Wat wil je? bijv. “blauwe randen, rode tekst en een hondenachtergrond”', pl='Czego chcesz? np. „niebieskie ramki, czerwony tekst i tapeta z psem”'),
    "The picture from the Ask AI card is always included; on: also {0}'s {1} wallpapers. Change them later on the new theme's wallpaper page.": dict(de='Das Bild von der KI-Karte ist immer dabei; an: auch die {1} Hintergründe von {0}. Später auf der Hintergrundseite des neuen Themes änderbar.', fr="L’image de la carte de l’IA est toujours incluse ; activé : aussi les {1} fonds de {0}. Modifiables plus tard sur la page des fonds d'écran du nouveau thème.", es='La imagen de la tarjeta de la IA siempre se incluye; activado: también los {1} fondos de {0}. Cámbialos luego en la página de fondos del nuevo tema.', it='L’immagine della scheda dell’IA è sempre inclusa; attivo: anche i {1} sfondi di {0}. Modificabili poi nella pagina sfondi del nuovo tema.', pt='A imagem do cartão da IA é sempre incluída; ligado: também os {1} papéis de parede de {0}. Altere-os depois na página de papéis de parede do novo tema.', nl='De afbeelding van de AI-kaart komt altijd mee; aan: ook de {1} achtergronden van {0}. Later te wijzigen op de achtergrondpagina van het nieuwe thema.', pl='Obraz z karty AI jest zawsze dołączony; włączone: także {1} tapet z {0}. Można je zmienić później na stronie tapet nowego motywu.'),
    'none signed in (claude auth login, codex login or agy)': dict(de='keiner angemeldet (claude auth login, codex login oder agy)', fr='aucun connecté (claude auth login, codex login ou agy)', es='ninguno con sesión (claude auth login, codex login o agy)', it='nessuno collegato (claude auth login, codex login o agy)', pt='nenhum com sessão (claude auth login, codex login ou agy)', nl='geen aangemeld (claude auth login, codex login of agy)', pl='żaden niezalogowany (claude auth login, codex login lub agy)'),
    'Save as suggestion': dict(de='Als Vorschlag speichern', fr='Enregistrer comme suggestion', es='Guardar como sugerencia', it='Salva come suggerimento', pt='Guardar como sugestão', nl='Opslaan als suggestie', pl='Zapisz jako sugestię'),
    'Edit suggestion': dict(de='Vorschlag bearbeiten', fr='Modifier la suggestion', es='Editar sugerencia', it='Modifica suggerimento', pt='Editar sugestão', nl='Suggestie bewerken', pl='Edytuj sugestię'),
    'Remove suggestion': dict(de='Vorschlag entfernen', fr='Supprimer la suggestion', es='Quitar sugerencia', it='Rimuovi suggerimento', pt='Remover sugestão', nl='Suggestie verwijderen', pl='Usuń sugestię'),
    'Back to the built-in suggestions': dict(de='Zurück zu den eingebauten Vorschlägen', fr='Revenir aux suggestions intégrées', es='Volver a las sugerencias integradas', it='Torna ai suggerimenti predefiniti', pt='Voltar às sugestões incluídas', nl='Terug naar de ingebouwde suggesties', pl='Przywróć wbudowane sugestie'),
    'Write a request first, then save it as a suggestion.': dict(de='Schreibe zuerst eine Anfrage und speichere sie dann als Vorschlag.', fr='Écrivez d’abord une demande, puis enregistrez-la comme suggestion.', es='Escribe primero una petición y luego guárdala como sugerencia.', it='Scrivi prima una richiesta, poi salvala come suggerimento.', pt='Escreva primeiro um pedido e depois guarde-o como sugestão.', nl='Schrijf eerst een verzoek en sla het dan op als suggestie.', pl='Najpierw napisz prośbę, potem zapisz ją jako sugestię.'),
    'That is already a suggestion.': dict(de='Das ist schon ein Vorschlag.', fr='C’est déjà une suggestion.', es='Eso ya es una sugerencia.', it='È già un suggerimento.', pt='Isso já é uma sugestão.', nl='Dat is al een suggestie.', pl='To już jest sugestia.'),
    'At most {0} suggestions: remove one first.': dict(de='Höchstens {0} Vorschläge: entferne zuerst einen.', fr='{0} suggestions au maximum : supprimez-en d’abord une.', es='Como máximo {0} sugerencias: quita una primero.', it='Al massimo {0} suggerimenti: rimuovine prima uno.', pt='No máximo {0} sugestões: remova primeiro uma.', nl='Hoogstens {0} suggesties: verwijder er eerst een.', pl='Najwyżej {0} sugestii: najpierw usuń jedną.'),
    'Saved as suggestion.': dict(de='Als Vorschlag gespeichert.', fr='Enregistré comme suggestion.', es='Guardado como sugerencia.', it='Salvato come suggerimento.', pt='Guardado como sugestão.', nl='Opgeslagen als suggestie.', pl='Zapisano jako sugestię.'),
    'A ? here and on the Ask AI page opens the note on easy AI setup with Maprisma.': dict(de='Ein ? hier und auf der Seite „KI fragen“ öffnet den Hinweis zur einfachen KI-Einrichtung mit Maprisma.', fr='Un ? ici et sur la page Demander à l’IA ouvre la note sur la configuration facile de l’IA avec Maprisma.', es='Un ? aquí y en la página Preguntar a la IA abre la nota sobre la configuración fácil de IA con Maprisma.', it='Un ? qui e nella pagina Chiedi all’IA apre la nota sulla configurazione facile dell’IA con Maprisma.', pt='Um ? aqui e na página Perguntar à IA abre a nota sobre a configuração fácil de IA com o Maprisma.', nl='Een ? hier en op de pagina AI vragen opent de notitie over eenvoudige AI-instelling met Maprisma.', pl='? tutaj i na stronie Zapytaj AI otwiera notatkę o łatwej konfiguracji AI z Maprisma.'),
    'Nothing visible changed. Try clearer words, e.g. “much darker”.': dict(de='Nichts Sichtbares hat sich geändert. Versuche deutlichere Worte, z. B. „viel dunkler“.', fr='Rien de visible n’a changé. Essayez des mots plus clairs, p. ex. « beaucoup plus sombre ».', es='No cambió nada visible. Prueba con palabras más claras, p. ej. «mucho más oscuro».', it='Non è cambiato nulla di visibile. Prova parole più chiare, es. «molto più scuro».', pt='Nada visível mudou. Tente palavras mais claras, ex.: “muito mais escuro”.', nl='Er is niets zichtbaars veranderd. Probeer duidelijkere woorden, bijv. “veel donkerder”.', pl='Nic widocznego się nie zmieniło. Spróbuj wyraźniejszych słów, np. „dużo ciemniej”.'),
    'Searching wallpapers…': dict(de='Suche Hintergründe…', fr='Recherche de fonds d’écran…', es='Buscando fondos…', it='Cerco sfondi…', pt='A procurar papéis de parede…', nl='Achtergronden zoeken…', pl='Szukam tapet…'),
    'Loading the picture…': dict(de='Lade das Bild…', fr='Chargement de l’image…', es='Cargando la imagen…', it='Carico l’immagine…', pt='A carregar a imagem…', nl='Afbeelding laden…', pl='Wczytuję obraz…'),
    'Refining with {0}…': dict(de='Verfeinere mit {0}…', fr='Affinage avec {0}…', es='Refinando con {0}…', it='Perfeziono con {0}…', pt='A refinar com {0}…', nl='Verfijnen met {0}…', pl='Dopracowuję z {0}…'),
    'Drag to reorder (Alt+↑/↓)': dict(de='Ziehen zum Umsortieren (Alt+↑/↓)', fr='Glisser pour réordonner (Alt+↑/↓)', es='Arrastra para reordenar (Alt+↑/↓)', it='Trascina per riordinare (Alt+↑/↓)', pt='Arraste para reordenar (Alt+↑/↓)', nl='Sleep om te herschikken (Alt+↑/↓)', pl='Przeciągnij, aby zmienić kolejność (Alt+↑/↓)'),
    'Clear the results': dict(de='Ergebnisse leeren', fr='Effacer les résultats', es='Borrar los resultados', it='Cancella i risultati', pt='Limpar os resultados', nl='Resultaten wissen', pl='Wyczyść wyniki'),
    '(current workspace)': dict(de='(aktueller Arbeitsbereich)', fr='(espace de travail actuel)', es='(espacio de trabajo actual)', it='(spazio di lavoro attuale)', pt='(área de trabalho atual)', nl='(huidig werkblad)', pl='(bieżący obszar roboczy)'),
    'Use on all workspaces': dict(de='Auf allen Arbeitsbereichen verwenden', fr='Utiliser sur tous les espaces de travail', es='Usar en todos los espacios de trabajo', it='Usa in tutti gli spazi di lavoro', pt='Usar em todas as áreas de trabalho', nl='Gebruiken op alle werkbladen', pl='Użyj we wszystkich obszarach roboczych'),
    "Removes the other workspaces' own themes: this one everywhere (until the next day/night switch)": dict(de='Entfernt die eigenen Themes der anderen Arbeitsbereiche: dieses überall (bis zum nächsten Tag/Nacht-Wechsel)', fr='Retire les thèmes propres aux autres espaces : celui-ci partout (jusqu’au prochain passage jour/nuit)', es='Quita los temas propios de los demás espacios: este en todos (hasta el próximo cambio día/noche)', it='Rimuove i temi propri degli altri spazi: questo ovunque (fino al prossimo cambio giorno/notte)', pt='Remove os temas próprios das outras áreas: este em todas (até à próxima mudança dia/noite)', nl='Verwijdert de eigen thema’s van de andere werkbladen: dit overal (tot de volgende dag/nacht-wissel)', pl='Usuwa własne motywy innych obszarów: ten wszędzie (do następnej zmiany dzień/noc)'),
    'Could not change the workspaces': dict(de='Die Arbeitsbereiche konnten nicht geändert werden', fr='Impossible de modifier les espaces de travail', es='No se pudieron cambiar los espacios de trabajo', it='Impossibile modificare gli spazi di lavoro', pt='Não foi possível alterar as áreas de trabalho', nl='De werkbladen konden niet worden gewijzigd', pl='Nie udało się zmienić obszarów roboczych'),
    "The workspaces' themes changed meanwhile.": dict(de='Die Themes der Arbeitsbereiche haben sich inzwischen geändert.', fr='Les thèmes des espaces de travail ont changé entre-temps.', es='Los temas de los espacios de trabajo cambiaron mientras tanto.', it='Nel frattempo i temi degli spazi di lavoro sono cambiati.', pt='Entretanto os temas das áreas de trabalho mudaram.', nl='De thema’s van de werkbladen zijn intussen veranderd.', pl='W międzyczasie motywy obszarów roboczych się zmieniły.'),
    'Every workspace uses {0}': dict(de='Alle Arbeitsbereiche verwenden {0}', fr='Tous les espaces de travail utilisent {0}', es='Todos los espacios de trabajo usan {0}', it='Tutti gli spazi di lavoro usano {0}', pt='Todas as áreas de trabalho usam {0}', nl='Alle werkbladen gebruiken {0}', pl='Wszystkie obszary robocze używają {0}'),
    'Autostart could not be confirmed for workspace {0}.': dict(de='Autostart für Arbeitsbereich {0} konnte nicht bestätigt werden.', fr='Le démarrage automatique de l’espace {0} n’a pas pu être confirmé.', es='No se pudo confirmar el inicio automático del espacio {0}.', it='Impossibile confermare l’avvio automatico dello spazio {0}.', pt='Não foi possível confirmar o arranque automático da área {0}.', nl='Autostart voor werkblad {0} kon niet worden bevestigd.', pl='Nie udało się potwierdzić autostartu obszaru {0}.'),
    'Stop': dict(de='Stopp', fr='Arrêter', es='Detener', it='Ferma', pt='Parar', nl='Stoppen', pl='Zatrzymaj'),
    'A helper that can search the web (now Claude Code) also looks for wallpaper pictures beyond wallhaven.cc. It takes longer and uses more of your plan or tokens. The pictures are downloaded only from public https addresses and checked before they are shown.': dict(de='Ein Helfer, der im Web suchen kann (derzeit Claude Code), sucht auch Hintergrundbilder außerhalb von wallhaven.cc. Das dauert länger und verbraucht mehr von deinem Abo oder deinen Tokens. Die Bilder werden nur von öffentlichen https-Adressen geladen und vor dem Anzeigen geprüft.', fr='Un assistant capable de chercher sur le web (actuellement Claude Code) cherche aussi des fonds d’écran au-delà de wallhaven.cc. C’est plus long et consomme davantage de votre forfait ou de vos jetons. Les images ne sont téléchargées que depuis des adresses https publiques et vérifiées avant d’être affichées.', es='Un asistente que puede buscar en la web (ahora Claude Code) también busca fondos fuera de wallhaven.cc. Tarda más y consume más de tu plan o tokens. Las imágenes solo se descargan de direcciones https públicas y se comprueban antes de mostrarse.', it='Un assistente che può cercare sul web (ora Claude Code) cerca anche sfondi oltre wallhaven.cc. Richiede più tempo e consuma più del tuo piano o dei tuoi token. Le immagini vengono scaricate solo da indirizzi https pubblici e controllate prima di essere mostrate.', pt='Um assistente que pode pesquisar na web (agora o Claude Code) também procura fundos para além de wallhaven.cc. Demora mais e usa mais do seu plano ou tokens. As imagens só são transferidas de endereços https públicos e verificadas antes de serem mostradas.', nl='Een helper die op het web kan zoeken (nu Claude Code) zoekt ook achtergronden buiten wallhaven.cc. Dat duurt langer en kost meer van je abonnement of tokens. De afbeeldingen worden alleen van openbare https-adressen gedownload en gecontroleerd voordat ze worden getoond.', pl='Pomocnik, który potrafi szukać w sieci (obecnie Claude Code), szuka też tapet poza wallhaven.cc. Trwa to dłużej i zużywa więcej Twojego planu lub tokenów. Obrazy są pobierane tylko z publicznych adresów https i sprawdzane przed wyświetleniem.'),
    'Ask AI failed: {0}': dict(de='Ask AI fehlgeschlagen: {0}', fr='Échec d’Ask AI : {0}', es='Ask AI falló: {0}', it='Ask AI non riuscito: {0}', pt='Ask AI falhou: {0}', nl='Ask AI mislukt: {0}', pl='Ask AI nie powiodło się: {0}'),
    'Ask AI, e.g. “calm and wintry, fits this theme”': dict(de='Ask AI, z. B. „ruhig und winterlich, passend zu diesem Theme“', fr='Ask AI, p. ex. « calme et hivernal, assorti à ce thème »', es='Ask AI, p. ej. «tranquilo e invernal, a juego con este tema»', it='Ask AI, ad es. «calmo e invernale, adatto a questo tema»', pt='Ask AI, p. ex. «calmo e invernal, a condizer com este tema»', nl='Ask AI, bijv. „rustig en winters, past bij dit thema”', pl='Ask AI, np. „spokojna i zimowa, pasująca do tego motywu”'),
    'Ask AI…': dict(de='Ask AI…', fr='Ask AI…', es='Ask AI…', it='Ask AI…', pt='Ask AI…', nl='Ask AI…', pl='Ask AI…'),
    "Ask {0} for search words: it gets your words and this theme's name and colors; the search itself uses no tokens": dict(de='{0} nach Suchwörtern fragen: Es bekommt deine Wörter sowie Name und Farben dieses Themes; die Suche selbst braucht keine Tokens', fr='Demander des mots de recherche à {0} : il reçoit vos mots ainsi que le nom et les couleurs de ce thème ; la recherche elle-même n’utilise aucun jeton', es='Pedir palabras de búsqueda a {0}: recibe tus palabras y el nombre y los colores de este tema; la búsqueda en sí no usa tokens', it='Chiedi a {0} parole di ricerca: riceve le tue parole e il nome e i colori di questo tema; la ricerca in sé non usa token', pt='Pedir palavras de pesquisa a {0}: recebe as suas palavras e o nome e as cores deste tema; a pesquisa em si não usa tokens', nl='{0} om zoekwoorden vragen: het krijgt je woorden en de naam en kleuren van dit thema; het zoeken zelf gebruikt geen tokens', pl='Zapytaj {0} o słowa wyszukiwania: otrzymuje Twoje słowa oraz nazwę i kolory tego motywu; samo wyszukiwanie nie zużywa tokenów'),
    'Asking {0} for wallpaper ideas and searching the web… (this can take a few minutes)': dict(de='Frage {0} nach Ideen für Hintergründe und suche im Web… (das kann einige Minuten dauern)', fr='Demande d’idées de fonds d’écran à {0} et recherche sur le web… (cela peut prendre quelques minutes)', es='Pidiendo a {0} ideas de fondos y buscando en la web… (puede tardar unos minutos)', it='Chiedo a {0} idee per sfondi e cerco sul web… (può richiedere qualche minuto)', pt='A pedir a {0} ideias de fundos e a pesquisar na web… (pode demorar alguns minutos)', nl='{0} vragen om ideeën voor achtergronden en op het web zoeken… (dit kan enkele minuten duren)', pl='Pytam {0} o pomysły na tapety i szukam w sieci… (może to potrwać kilka minut)'),
    'Asking {0} for wallpaper ideas…': dict(de='Frage {0} nach Ideen für Hintergründe…', fr='Demande d’idées de fonds d’écran à {0}…', es='Pidiendo a {0} ideas de fondos…', it='Chiedo a {0} idee per sfondi…', pt='A pedir a {0} ideias de fundos…', nl='{0} vragen om ideeën voor achtergronden…', pl='Pytam {0} o pomysły na tapety…'),
    'Describe the wallpaper you want: Ask AI turns it into searches on wallhaven.cc': dict(de='Beschreibe den gewünschten Hintergrund: Ask AI macht daraus Suchen auf wallhaven.cc', fr='Décrivez le fond d’écran souhaité : Ask AI en fait des recherches sur wallhaven.cc', es='Describe el fondo que quieres: Ask AI lo convierte en búsquedas en wallhaven.cc', it='Descrivi lo sfondo che vuoi: Ask AI lo trasforma in ricerche su wallhaven.cc', pt='Descreva o fundo que quer: o Ask AI transforma-o em pesquisas no wallhaven.cc', nl='Beschrijf de achtergrond die je wilt: Ask AI maakt er zoekopdrachten op wallhaven.cc van', pl='Opisz tapetę, której szukasz: Ask AI zamieni to w wyszukiwania na wallhaven.cc'),
    'Search for “{0}”': dict(de='Nach „{0}“ suchen', fr='Rechercher « {0} »', es='Buscar «{0}»', it='Cerca «{0}»', pt='Pesquisar «{0}»', nl='Zoeken naar „{0}”', pl='Szukaj „{0}”'),
    'Search the web for wallpapers': dict(de='Im Web nach Hintergründen suchen', fr='Chercher des fonds d’écran sur le web', es='Buscar fondos en la web', it='Cerca sfondi sul web', pt='Pesquisar fundos na web', nl='Op het web naar achtergronden zoeken', pl='Szukaj tapet w sieci'),
    'no usable search words in the answer': dict(de='keine brauchbaren Suchwörter in der Antwort', fr='aucun mot de recherche utilisable dans la réponse', es='no hay palabras de búsqueda útiles en la respuesta', it='nessuna parola di ricerca utilizzabile nella risposta', pt='nenhuma palavra de pesquisa utilizável na resposta', nl='geen bruikbare zoekwoorden in het antwoord', pl='brak użytecznych słów wyszukiwania w odpowiedzi'),
    'not a public https image address': dict(de='keine öffentliche https-Bildadresse', fr='pas une adresse d’image https publique', es='no es una dirección https pública de imagen', it='non è un indirizzo https pubblico di un’immagine', pt='não é um endereço https público de imagem', nl='geen openbaar https-adres van een afbeelding', pl='to nie jest publiczny adres https obrazu'),
    'the link is not a JPEG, PNG or WebP picture': dict(de='der Link ist kein JPEG-, PNG- oder WebP-Bild', fr='le lien n’est pas une image JPEG, PNG ou WebP', es='el enlace no es una imagen JPEG, PNG o WebP', it='il link non è un’immagine JPEG, PNG o WebP', pt='a ligação não é uma imagem JPEG, PNG ou WebP', nl='de link is geen JPEG-, PNG- of WebP-afbeelding', pl='link nie jest obrazem JPEG, PNG ani WebP'),
    'web': dict(de='Web', fr='web', es='web', it='web', pt='web', nl='web', pl='sieć'),
    '{0} could not be found': dict(de='{0} wurde nicht gefunden', fr='{0} est introuvable', es='No se encontró {0}', it='{0} non trovato', pt='{0} não foi encontrado', nl='{0} niet gevonden', pl='Nie znaleziono {0}'),
    'the picture cache {0} is not your own folder': dict(de='der Bild-Cache {0} ist nicht dein eigener Ordner', fr='le cache d’images {0} n’est pas votre propre dossier', es='la caché de imágenes {0} no es tu propia carpeta', it='la cache delle immagini {0} non è una tua cartella', pt='o cache de imagens {0} não é a sua própria pasta', nl='de afbeeldingencache {0} is niet je eigen map', pl='pamięć podręczna obrazów {0} nie jest twoim folderem'),
    '{0} is not a public web address': dict(de='{0} ist keine öffentliche Webadresse', fr='{0} n’est pas une adresse web publique', es='{0} no es una dirección web pública', it='{0} non è un indirizzo web pubblico', pt='{0} não é um endereço web público', nl='{0} is geen openbaar webadres', pl='{0} nie jest publicznym adresem w sieci'),
    '{0} pictures from the web.': dict(de='{0} Bilder aus dem Web.', fr='{0} images du web.', es='{0} imágenes de la web.', it='{0} immagini dal web.', pt='{0} imagens da web.', nl='{0} afbeeldingen van het web.', pl='{0} obrazów z sieci.'),
    ' Next shows more.': dict(de=' Weiter zeigt mehr.', fr=' Suivant en montre plus.', es=' Siguiente muestra más.', it=' Avanti ne mostra altri.', pt=' Seguinte mostra mais.', nl=' Volgende toont meer.', pl=' Dalej pokazuje więcej.'),
    'Asking {0} for more pictures from the web…': dict(de='Frage {0} nach weiteren Bildern aus dem Web…', fr='Demande d’autres images du web à {0}…', es='Pidiendo a {0} más imágenes de la web…', it='Chiedo a {0} altre immagini dal web…', pt='A pedir a {0} mais imagens da web…', nl='{0} vragen om meer afbeeldingen van het web…', pl='Pytam {0} o kolejne obrazy z sieci…'),
    'Next {0}': dict(de='Weitere {0}', fr='{0} suivants', es='Siguientes {0}', it='Altri {0}', pt='Seguintes {0}', nl='Volgende {0}', pl='Dalej {0}'),
    'No more pictures from the web.': dict(de='Keine weiteren Bilder aus dem Web.', fr='Plus d’autres images du web.', es='No hay más imágenes de la web.', it='Nessun’altra immagine dal web.', pt='Não há mais imagens da web.', nl='Geen afbeeldingen meer van het web.', pl='Brak kolejnych obrazów z sieci.'),
    'Searching {0} for “{1}”…': dict(de='Suche auf {0} nach „{1}“…', fr='Recherche sur {0} de « {1} »…', es='Buscando en {0} «{1}»…', it='Ricerca su {0} di «{1}»…', pt='A pesquisar em {0} por «{1}»…', nl='Op {0} zoeken naar ‘{1}’…', pl='Wyszukiwanie „{1}” w {0}…'),
    '{0} more from the web (asks {1} again)': dict(de='{0} weitere aus dem Web (fragt {1} erneut)', fr='{0} de plus depuis le web (redemande à {1})', es='{0} más de la web (pregunta otra vez a {1})', it='Altri {0} dal web (richiede di nuovo a {1})', pt='Mais {0} da web (pergunta de novo a {1})', nl='Nog {0} van het web (vraagt {1} opnieuw)', pl='Kolejne {0} z sieci (ponownie pyta {1})'),
    '{0} more from {1}': dict(de='{0} weitere von {1}', fr='{0} de plus depuis {1}', es='{0} más de {1}', it='Altri {0} da {1}', pt='Mais {0} de {1}', nl='Nog {0} van {1}', pl='Kolejne {0} z {1}'),
    '{0} more pictures from the web.': dict(de='{0} weitere Bilder aus dem Web.', fr='{0} images de plus depuis le web.', es='{0} imágenes más de la web.', it='Altre {0} immagini dal web.', pt='Mais {0} imagens da web.', nl='Nog {0} afbeeldingen van het web.', pl='Kolejne {0} obrazów z sieci.'),
    '{0} of {1}+ wallpapers for “{2}”.{3}': dict(de='{0} von {1}+ Hintergründen für „{2}“.{3}', fr='{0} sur {1}+ fonds d’écran pour « {2} ».{3}', es='{0} de {1}+ fondos para «{2}».{3}', it='{0} di {1}+ sfondi per «{2}».{3}', pt='{0} de {1}+ fundos para «{2}».{3}', nl='{0} van {1}+ achtergronden voor „{2}”.{3}', pl='{0} z {1}+ tapet dla „{2}”.{3}'),
    '{0} pictures': dict(de='{0} Bilder', fr='{0} images', es='{0} imágenes', it='{0} immagini', pt='{0} imagens', nl='{0} afbeeldingen', pl='{0} obrazów'),
    'Pictures per batch': dict(de='Bilder pro Durchgang', fr='Images par lot', es='Imágenes por tanda', it='Immagini per gruppo', pt='Imagens por lote', nl='Afbeeldingen per reeks', pl='Obrazy na partię'),
    'Pictures per batch: from the web and from wallhaven.cc; Next shows the next batch': dict(de='Bilder pro Durchgang: aus dem Web und von wallhaven.cc; „Weitere“ zeigt den nächsten Durchgang', fr='Images par lot : du web et de wallhaven.cc ; « Suivants » affiche le lot suivant', es='Imágenes por tanda: de la web y de wallhaven.cc; «Siguientes» muestra la siguiente tanda', it='Immagini per gruppo: dal web e da wallhaven.cc; «Altri» mostra il gruppo successivo', pt='Imagens por lote: da web e de wallhaven.cc; «Seguintes» mostra o lote seguinte', nl='Afbeeldingen per reeks: van het web en van wallhaven.cc; Volgende toont de volgende reeks', pl='Obrazy na partię: z sieci i z wallhaven.cc; Dalej pokazuje następną partię'),
    'Cancelled.': dict(de='Abgebrochen.', fr='Annulé.', es='Cancelado.', it='Annullato.', pt='Cancelado.', nl='Geannuleerd.', pl='Anulowano.'),
    'Adds it to {0} and sets it as the background': dict(de='Fügt es zu {0} hinzu und setzt es als Hintergrund', fr='L’ajoute à {0} et le définit comme fond d’écran', es='Lo añade a {0} y lo pone como fondo', it='Lo aggiunge a {0} e lo imposta come sfondo', pt='Adiciona-o a {0} e define-o como fundo', nl='Voegt het toe aan {0} en zet het als achtergrond', pl='Dodaje do {0} i ustawia jako tło'),
    'Adds it to {0}, applies {0} and sets it as the background': dict(de='Fügt es zu {0} hinzu, wendet {0} an und setzt es als Hintergrund', fr='L’ajoute à {0}, applique {0} et le définit comme fond d’écran', es='Lo añade a {0}, aplica {0} y lo pone como fondo', it='Lo aggiunge a {0}, applica {0} e lo imposta come sfondo', pt='Adiciona-o a {0}, aplica {0} e define-o como fundo', nl='Voegt het toe aan {0}, past {0} toe en zet het als achtergrond', pl='Dodaje do {0}, stosuje {0} i ustawia jako tło'),
    'Applied {0} with {1}.': dict(de='{0} mit {1} angewendet.', fr='{0} appliqué avec {1}.', es='{0} aplicado con {1}.', it='{0} applicato con {1}.', pt='{0} aplicado com {1}.', nl='{0} toegepast met {1}.', pl='Zastosowano {0} z {1}.'),
    'Applying {0}…': dict(de='{0} wird angewendet…', fr='Application de {0}…', es='Aplicando {0}…', it='Applicazione di {0}…', pt='A aplicar {0}…', nl='{0} toepassen…', pl='Stosowanie {0}…'),
    'Could not apply {0}: {1}': dict(de='{0} konnte nicht angewendet werden: {1}', fr='Impossible d’appliquer {0} : {1}', es='No se pudo aplicar {0}: {1}', it='Impossibile applicare {0}: {1}', pt='Não foi possível aplicar {0}: {1}', nl='Kon {0} niet toepassen: {1}', pl='Nie udało się zastosować {0}: {1}'),
    'Could not set background': dict(de='Hintergrund konnte nicht gesetzt werden', fr='Impossible de définir le fond', es='No se pudo establecer el fondo', it='Impossibile impostare lo sfondo', pt='Não foi possível definir o papel de parede', nl='Kon de achtergrond niet instellen', pl='Nie można ustawić tapety'),
}


# Font & effects per theme (theme_looks).
LOOK_TRANSLATIONS = {
    'Font & effects…': dict(de='Schrift & Effekte…', fr='Police et effets…', es='Fuente y efectos…', it='Carattere ed effetti…', pt='Fonte e efeitos…', nl='Lettertype en effecten…', pl='Czcionka i efekty…'),
    'Font & effects — {0}': dict(de='Schrift & Effekte — {0}', fr='Police et effets — {0}', es='Fuente y efectos — {0}', it='Carattere ed effetti — {0}', pt='Fonte e efeitos — {0}', nl='Lettertype en effecten — {0}', pl='Czcionka i efekty — {0}'),
    'Your own font and window style whenever this theme is applied': dict(de='Deine eigene Schrift und Fensterart, wann immer dieses Theme angewendet wird', fr='Votre propre police et style de fenêtres chaque fois que ce thème est appliqué', es='Tu propia fuente y estilo de ventanas cada vez que se aplica este tema', it='Il tuo carattere e stile delle finestre ogni volta che questo tema viene applicato', pt='A sua própria fonte e estilo de janelas sempre que este tema é aplicado', nl='Je eigen lettertype en vensterstijl telkens als dit thema wordt toegepast', pl='Własna czcionka i styl okien przy każdym zastosowaniu tego motywu'),
    'Used whenever this theme is applied; the theme files stay as they are. Themes without a choice give your own font and window style back.': dict(de='Gilt, wann immer dieses Theme angewendet wird; die Theme-Dateien bleiben unverändert. Themes ohne Auswahl geben dir deine eigene Schrift und Fensterart zurück.', fr='Utilisé chaque fois que ce thème est appliqué ; les fichiers du thème restent inchangés. Les thèmes sans choix vous rendent votre propre police et style de fenêtres.', es='Se usa cada vez que se aplica este tema; los archivos del tema no cambian. Los temas sin elección te devuelven tu propia fuente y estilo de ventanas.', it='Usato ogni volta che questo tema viene applicato; i file del tema restano invariati. I temi senza scelta ti restituiscono il tuo carattere e stile delle finestre.', pt='Usado sempre que este tema é aplicado; os ficheiros do tema ficam como estão. Temas sem escolha devolvem a sua própria fonte e estilo de janelas.', nl='Gebruikt telkens als dit thema wordt toegepast; de themabestanden blijven ongewijzigd. Thema’s zonder keuze geven je eigen lettertype en vensterstijl terug.', pl='Używane przy każdym zastosowaniu tego motywu; pliki motywu pozostają bez zmian. Motywy bez wyboru przywracają Twoją czcionkę i styl okien.'),
    "Omarchy's monospace font: terminals, the bar and menus": dict(de='Omarchys Monospace-Schrift: Terminals, Leiste und Menüs', fr='La police à chasse fixe d’Omarchy : terminaux, barre et menus', es='La fuente monoespaciada de Omarchy: terminales, barra y menús', it='Il carattere a spaziatura fissa di Omarchy: terminali, barra e menu', pt='A fonte monoespaçada do Omarchy: terminais, barra e menus', nl='Het monospace-lettertype van Omarchy: terminals, balk en menu’s', pl='Czcionka o stałej szerokości Omarchy: terminale, pasek i menu'),
    'Windows': dict(de='Fenster', fr='Fenêtres', es='Ventanas', it='Finestre', pt='Janelas', nl='Vensters', pl='Okna'),
    'Glassy blurs what is behind see-through windows, the bar and menus, and gives terminals a see-through background': dict(de='Glas macht alles hinter durchsichtigen Fenstern, der Leiste und Menüs unscharf und gibt Terminals einen durchsichtigen Hintergrund', fr='Verre floute ce qui se trouve derrière les fenêtres transparentes, la barre et les menus, et donne aux terminaux un fond transparent', es='Cristal desenfoca lo que hay detrás de ventanas transparentes, la barra y los menús, y da a los terminales un fondo transparente', it='Vetro sfoca ciò che sta dietro finestre trasparenti, barra e menu, e dà ai terminali uno sfondo trasparente', pt='Vidro desfoca o que está atrás de janelas transparentes, da barra e dos menus, e dá aos terminais um fundo transparente', nl='Glas vervaagt wat achter doorzichtige vensters, de balk en menu’s ligt, en geeft terminals een doorzichtige achtergrond', pl='Szkło rozmywa to, co za przezroczystymi oknami, paskiem i menu, i daje terminalom przezroczyste tło'),
    'Opaque': dict(de='Deckend', fr='Opaques', es='Opacas', it='Opache', pt='Opacas', nl='Ondoorzichtig', pl='Nieprzezroczyste'),
    'Transparent': dict(de='Transparent', fr='Transparentes', es='Transparentes', it='Trasparenti', pt='Transparentes', nl='Transparant', pl='Przezroczyste'),
    'Glassy (blur)': dict(de='Glas (unscharf)', fr='Verre (flou)', es='Cristal (desenfoque)', it='Vetro (sfocatura)', pt='Vidro (desfoque)', nl='Glas (vervaging)', pl='Szkło (rozmycie)'),
    'font': dict(de='Schrift', fr='police', es='fuente', it='carattere', pt='fonte', nl='lettertype', pl='czcionka'),
    'window style': dict(de='Fensterart', fr='style de fenêtres', es='estilo de ventanas', it='stile delle finestre', pt='estilo de janelas', nl='vensterstijl', pl='styl okien'),
    'Theme default': dict(de='Theme-Standard', fr='Par défaut du thème', es='Predeterminado del tema', it='Predefinito del tema', pt='Predefinição do tema', nl='Themastandaard', pl='Domyślne motywu'),
    'Theme default ({0})': dict(de='Theme-Standard ({0})', fr='Par défaut du thème ({0})', es='Predeterminado del tema ({0})', it='Predefinito del tema ({0})', pt='Predefinição do tema ({0})', nl='Themastandaard ({0})', pl='Domyślne motywu ({0})'),
    'Theme default (yours: {0})': dict(de='Theme-Standard (deine: {0})', fr='Par défaut du thème (le vôtre : {0})', es='Predeterminado del tema (el tuyo: {0})', it='Predefinito del tema (il tuo: {0})', pt='Predefinição do tema (a sua: {0})', nl='Themastandaard (die van jou: {0})', pl='Domyślne motywu (Twoje: {0})'),
    '{0} (not installed)': dict(de='{0} (nicht installiert)', fr='{0} (non installée)', es='{0} (no instalada)', it='{0} (non installato)', pt='{0} (não instalada)', nl='{0} (niet geïnstalleerd)', pl='{0} (niezainstalowana)'),
    'Reapply': dict(de='Erneut anwenden', fr='Réappliquer', es='Volver a aplicar', it='Riapplica', pt='Reaplicar', nl='Opnieuw toepassen', pl='Zastosuj ponownie'),
    'Not on the desktop yet: {0}': dict(de='Noch nicht auf dem Desktop: {0}', fr='Pas encore sur le bureau : {0}', es='Aún no en el escritorio: {0}', it='Non ancora sul desktop: {0}', pt='Ainda não no ambiente de trabalho: {0}', nl='Nog niet op het bureaublad: {0}', pl='Jeszcze nie na pulpicie: {0}'),
    'Loading…': dict(de='Lade…', fr='Chargement…', es='Cargando…', it='Caricamento…', pt='A carregar…', nl='Laden…', pl='Wczytywanie…'),
    "Could not read the theme's look: {0}": dict(de='Schrift & Effekte des Themes konnten nicht gelesen werden: {0}', fr='Impossible de lire la police et les effets du thème : {0}', es='No se pudieron leer la fuente y los efectos del tema: {0}', it='Impossibile leggere carattere ed effetti del tema: {0}', pt='Não foi possível ler a fonte e os efeitos do tema: {0}', nl='Lettertype en effecten van het thema konden niet worden gelezen: {0}', pl='Nie udało się odczytać czcionki i efektów motywu: {0}'),
    'Could not save Font & effects': dict(de='Schrift & Effekte konnten nicht gespeichert werden', fr='Impossible d’enregistrer la police et les effets', es='No se pudieron guardar la fuente y los efectos', it='Impossibile salvare carattere ed effetti', pt='Não foi possível guardar a fonte e os efeitos', nl='Lettertype en effecten konden niet worden opgeslagen', pl='Nie udało się zapisać czcionki i efektów'),
    'Saves the change and puts it on the desktop now': dict(de='Speichert die Änderung und bringt sie jetzt auf den Desktop', fr='Enregistre la modification et l’applique maintenant au bureau', es='Guarda el cambio y lo aplica ya al escritorio', it='Salva la modifica e la applica subito al desktop', pt='Guarda a alteração e aplica-a já no ambiente de trabalho', nl='Slaat de wijziging op en zet die nu op het bureaublad', pl='Zapisuje zmianę i od razu stosuje ją na pulpicie'),
    'Saves the change; it is used whenever this theme is applied': dict(de='Speichert die Änderung; sie gilt, wann immer dieses Theme angewendet wird', fr='Enregistre la modification ; elle s’applique chaque fois que ce thème est appliqué', es='Guarda el cambio; se usa cada vez que se aplica este tema', it='Salva la modifica; vale ogni volta che questo tema viene applicato', pt='Guarda a alteração; é usada sempre que este tema é aplicado', nl='Slaat de wijziging op; die geldt telkens als dit thema wordt toegepast', pl='Zapisuje zmianę; obowiązuje przy każdym zastosowaniu tego motywu'),
    'Font & effects saved and applied': dict(de='Schrift & Effekte gespeichert und angewendet', fr='Police et effets enregistrés et appliqués', es='Fuente y efectos guardados y aplicados', it='Carattere ed effetti salvati e applicati', pt='Fonte e efeitos guardados e aplicados', nl='Lettertype en effecten opgeslagen en toegepast', pl='Zapisano i zastosowano czcionkę i efekty'),
    'Font & effects saved for {0}': dict(de='Schrift & Effekte für {0} gespeichert', fr='Police et effets enregistrés pour {0}', es='Fuente y efectos guardados para {0}', it='Carattere ed effetti salvati per {0}', pt='Fonte e efeitos guardados para {0}', nl='Lettertype en effecten opgeslagen voor {0}', pl='Zapisano czcionkę i efekty dla {0}'),
    'Could not reapply': dict(de='Konnte nicht erneut angewendet werden', fr='Impossible de réappliquer', es='No se pudo volver a aplicar', it='Impossibile riapplicare', pt='Não foi possível reaplicar', nl='Kon niet opnieuw worden toegepast', pl='Nie udało się zastosować ponownie'),
    'Keep the current window style': dict(de='Aktuelle Fensterart behalten', fr='Garder le style de fenêtres actuel', es='Mantener el estilo de ventanas actual', it='Mantieni lo stile delle finestre attuale', pt='Manter o estilo de janelas atual', nl='Huidige vensterstijl behouden', pl='Zachowaj obecny styl okien'),
    "Omarchy's window style. Glassy blurs what is behind see-through windows, the bar and menus. It is switched when this theme is applied, and back to your own with other themes.": dict(de='Omarchys Fensterart. Glas macht alles hinter durchsichtigen Fenstern, der Leiste und Menüs unscharf. Sie wird beim Anwenden dieses Themes umgestellt und bei anderen Themes wieder auf deine eigene.', fr='Le style de fenêtres d’Omarchy. Verre floute ce qui se trouve derrière les fenêtres transparentes, la barre et les menus. Il change quand ce thème est appliqué, et revient au vôtre avec les autres thèmes.', es='El estilo de ventanas de Omarchy. Cristal desenfoca lo que hay detrás de ventanas transparentes, la barra y los menús. Cambia al aplicar este tema y vuelve al tuyo con otros temas.', it='Lo stile delle finestre di Omarchy. Vetro sfoca ciò che sta dietro finestre trasparenti, barra e menu. Cambia quando si applica questo tema e torna al tuo con gli altri temi.', pt='O estilo de janelas do Omarchy. Vidro desfoca o que está atrás de janelas transparentes, da barra e dos menus. Muda ao aplicar este tema e volta ao seu com outros temas.', nl='De vensterstijl van Omarchy. Glas vervaagt wat achter doorzichtige vensters, de balk en menu’s ligt. Hij wisselt als dit thema wordt toegepast, en terug naar die van jou bij andere thema’s.', pl='Styl okien Omarchy. Szkło rozmywa to, co za przezroczystymi oknami, paskiem i menu. Zmienia się po zastosowaniu tego motywu i wraca do Twojego przy innych motywach.'),
    'bad window style': dict(de='ungültige Fensterart', fr='style de fenêtres invalide', es='estilo de ventanas no válido', it='stile delle finestre non valido', pt='estilo de janelas inválido', nl='ongeldige vensterstijl', pl='nieprawidłowy styl okien'),
    'New theme': dict(de='Neues Theme', fr='Nouveau thème', es='Tema nuevo', it='Nuovo tema', pt='Novo tema', nl='Nieuw thema', pl='Nowy motyw'),
    'Presets: saved theme and wallpaper': dict(de='Presets: gespeichertes Theme und Hintergrund', fr='Préréglages : thème et fond d’écran enregistrés', es='Preajustes: tema y fondo guardados', it='Preimpostazioni: tema e sfondo salvati', pt='Presets: tema e papel de parede guardados', nl='Voorinstellingen: opgeslagen thema en achtergrond', pl='Presety: zapisany motyw i tapeta'),
    'Save the current theme and wallpaper for later.': dict(de='Aktuelles Theme und Hintergrund für später speichern.', fr='Enregistrer le thème et le fond d’écran actuels pour plus tard.', es='Guarda el tema y el fondo actuales para más tarde.', it='Salva tema e sfondo attuali per dopo.', pt='Guardar o tema e o papel de parede atuais para mais tarde.', nl='Huidig thema en achtergrond bewaren voor later.', pl='Zapisz bieżący motyw i tapetę na później.'),
    'Reapplied: {0}': dict(de='Erneut angewendet: {0}', fr='Réappliqué : {0}', es='Aplicado de nuevo: {0}', it='Riapplicato: {0}', pt='Reaplicado: {0}', nl='Opnieuw toegepast: {0}', pl='Zastosowano ponownie: {0}'),
}


# Settings › Corners: one setting for every control (2.2.0), labels as in AI Playground.
CORNER_TRANSLATIONS = {
    'Slightly rounded ({0} px)': dict(de='Leicht abgerundet ({0} px)', fr='Légèrement arrondis ({0} px)', es='Ligeramente redondeadas ({0} px)', it='Leggermente arrotondati ({0} px)', pt='Levemente arredondados ({0} px)', nl='Licht afgerond ({0} px)', pl='Lekko zaokrąglone ({0} px)'),
    'Rounded ({0} px)': dict(de='Abgerundet ({0} px)', fr='Arrondis ({0} px)', es='Redondeadas ({0} px)', it='Arrotondati ({0} px)', pt='Arredondados ({0} px)', nl='Afgerond ({0} px)', pl='Zaokrąglone ({0} px)'),
    "Corners of the popup, its cards and every control (buttons, choice boxes, fields). “Like windows” follows Hyprland's window rounding.": dict(
        de='Ecken des Popups, seiner Karten und aller Bedienelemente (Knöpfe, Auswahlfelder, Eingabefelder). „Wie Fenster“ folgt der Fensterrundung von Hyprland.',
        fr="Coins de la fenêtre, de ses cartes et de toutes les commandes (boutons, listes de choix, champs). « Comme les fenêtres » suit l'arrondi des fenêtres de Hyprland.",
        es='Esquinas de la ventana, sus tarjetas y todos los controles (botones, listas de opciones, campos). «Como las ventanas» sigue el redondeo de ventanas de Hyprland.',
        it="Angoli della finestra, delle sue schede e di tutti i controlli (pulsanti, caselle di scelta, campi). «Come le finestre» segue l'arrotondamento delle finestre di Hyprland.",
        pt='Cantos da janela, dos seus cartões e de todos os controlos (botões, caixas de escolha, campos). «Como as janelas» segue o arredondamento das janelas do Hyprland.',
        nl='Hoeken van de pop-up, zijn kaarten en alle bedieningselementen (knoppen, keuzelijsten, velden). „Zoals vensters” volgt de vensterafronding van Hyprland.',
        pl='Narożniki okna, jego kart i wszystkich kontrolek (przyciski, listy wyboru, pola). „Jak okna” podąża za zaokrągleniem okien Hyprlanda.'),
}


# The microphone beside the Ask AI fields (dictation.py).
EXTRA["Speak instead of typing: {0} writes what you say at the cursor. Click again to stop."] = dict(
    de="Sprechen statt tippen: {0} schreibt, was du sagst, an die Stelle des Cursors. Noch einmal klicken zum Beenden.",
    fr="Parler au lieu de taper : {0} écrit ce que tu dis à l'endroit du curseur. Clique encore pour arrêter.",
    es="Hablar en lugar de escribir: {0} escribe lo que dices donde está el cursor. Haz clic otra vez para parar.",
    it="Parla invece di scrivere: {0} scrive quello che dici dove si trova il cursore. Clicca di nuovo per fermare.",
    pt="Falar em vez de escrever: {0} escreve o que dizes onde está o cursor. Clica outra vez para parar.",
    nl="Spreken in plaats van typen: {0} schrijft wat je zegt op de plek van de cursor. Klik nog eens om te stoppen.",
    pl="Mów zamiast pisać: {0} wpisuje to, co mówisz, w miejscu kursora. Kliknij ponownie, aby zakończyć.")

# The dictation set-up hint (dictation.py).
EXTRA['Set up dictation'] = {'de': 'Diktat einrichten', 'fr': 'Configurer la dictée', 'es': 'Configurar el dictado', 'it': 'Configura la dettatura', 'pt': 'Configurar o ditado', 'nl': 'Dicteren instellen', 'pl': 'Skonfiguruj dyktowanie'}
EXTRA["To speak instead of typing, choose a dictation program first: in Omarchy's menu under Setup › Defaults › Dictation, pick Superwhisper or Voxtype; Omarchy installs and sets it up. Then click the microphone again."] = {'de': 'Um zu sprechen statt zu tippen, brauchst du zuerst ein Diktatprogramm: Wähle im Omarchy-Menü unter Setup › Defaults › Dictation entweder Superwhisper oder Voxtype; Omarchy installiert und richtet es ein. Klicke danach noch einmal auf das Mikrofon.', 'fr': "Pour parler au lieu de taper, choisis d'abord un programme de dictée : dans le menu d'Omarchy, sous Setup › Defaults › Dictation, prends Superwhisper ou Voxtype ; Omarchy l'installe et le configure. Clique ensuite de nouveau sur le micro.", 'es': 'Para hablar en lugar de escribir, elige primero un programa de dictado: en el menú de Omarchy, en Setup › Defaults › Dictation, elige Superwhisper o Voxtype; Omarchy lo instala y lo configura. Después haz clic de nuevo en el micrófono.', 'it': 'Per parlare invece di scrivere, scegli prima un programma di dettatura: nel menu di Omarchy, in Setup › Defaults › Dictation, scegli Superwhisper o Voxtype; Omarchy lo installa e lo configura. Poi clicca di nuovo sul microfono.', 'pt': 'Para falar em vez de escrever, escolhe primeiro um programa de ditado: no menu do Omarchy, em Setup › Defaults › Dictation, escolhe Superwhisper ou Voxtype; o Omarchy instala-o e configura-o. Depois clica outra vez no microfone.', 'nl': 'Om te spreken in plaats van te typen, kies je eerst een dicteerprogramma: in het Omarchy-menu onder Setup › Defaults › Dictation kies je Superwhisper of Voxtype; Omarchy installeert en stelt het in. Klik daarna nog eens op de microfoon.', 'pl': 'Aby mówić zamiast pisać, wybierz najpierw program do dyktowania: w menu Omarchy w Setup › Defaults › Dictation wybierz Superwhisper lub Voxtype; Omarchy go zainstaluje i skonfiguruje. Potem kliknij ponownie mikrofon.'}
EXTRA['Open dictation setup'] = {'de': 'Diktat-Einrichtung öffnen', 'fr': 'Ouvrir la configuration de la dictée', 'es': 'Abrir la configuración del dictado', 'it': 'Apri la configurazione della dettatura', 'pt': 'Abrir a configuração do ditado', 'nl': 'Dicteerinstellingen openen', 'pl': 'Otwórz konfigurację dyktowania'}
EXTRA['Speak instead of typing: no dictation program is set up yet. Click for how to set one up.'] = {'de': 'Sprechen statt tippen: Es ist noch kein Diktatprogramm eingerichtet. Klicke, um zu sehen, wie du eines einrichtest.', 'fr': "Parler au lieu de taper : aucun programme de dictée n'est encore configuré. Clique pour savoir comment en configurer un.", 'es': 'Hablar en lugar de escribir: todavía no hay ningún programa de dictado configurado. Haz clic para ver cómo configurar uno.', 'it': 'Parla invece di scrivere: non è ancora configurato nessun programma di dettatura. Clicca per vedere come configurarne uno.', 'pt': 'Falar em vez de escrever: ainda não há nenhum programa de ditado configurado. Clica para ver como configurar um.', 'nl': 'Spreken in plaats van typen: er is nog geen dicteerprogramma ingesteld. Klik om te zien hoe je er een instelt.', 'pl': 'Mów zamiast pisać: nie skonfigurowano jeszcze żadnego programu do dyktowania. Kliknij, aby zobaczyć, jak go skonfigurować.'}
EXTRA['Text colour'] = {'de': 'Textfarbe', 'fr': 'Couleur du texte', 'es': 'Color del texto', 'it': 'Colore del testo', 'pt': 'Cor do texto', 'nl': 'Tekstkleur', 'pl': 'Kolor tekstu'}
EXTRA['text colour'] = {'de': 'Textfarbe', 'fr': 'couleur du texte', 'es': 'color del texto', 'it': 'colore del testo', 'pt': 'cor do texto', 'nl': 'tekstkleur', 'pl': 'kolor tekstu'}
EXTRA["Theme's own"] = {'de': 'Wie im Theme', 'fr': 'Celle du thème', 'es': 'La del tema', 'it': 'Quello del tema', 'pt': 'A do tema', 'nl': 'Van het thema', 'pl': 'Z motywu'}
EXTRA["Theme's own: {0}"] = {'de': 'Wie im Theme: {0}', 'fr': 'Celle du thème : {0}', 'es': 'La del tema: {0}', 'it': 'Quello del tema: {0}', 'pt': 'A do tema: {0}', 'nl': 'Van het thema: {0}', 'pl': 'Z motywu: {0}'}
EXTRA["The theme's own text colour, {0}"] = {'de': 'Die Textfarbe des Themes, {0}', 'fr': 'La couleur du texte du thème, {0}', 'es': 'El color del texto del tema, {0}', 'it': 'Il colore del testo del tema, {0}', 'pt': 'A cor do texto do tema, {0}', 'nl': 'De tekstkleur van het thema, {0}', 'pl': 'Kolor tekstu motywu, {0}'}
EXTRA['{0} for this theme'] = {'de': '{0} für dieses Theme', 'fr': '{0} pour ce thème', 'es': '{0} para este tema', 'it': '{0} per questo tema', 'pt': '{0} para este tema', 'nl': '{0} voor dit thema', 'pl': '{0} dla tego motywu'}
EXTRA['Use as text colour'] = {'de': 'Als Textfarbe verwenden', 'fr': 'Utiliser comme couleur du texte', 'es': 'Usar como color del texto', 'it': 'Usa come colore del testo', 'pt': 'Usar como cor do texto', 'nl': 'Gebruiken als tekstkleur', 'pl': 'Użyj jako koloru tekstu'}
EXTRA["Hard to read on this theme's background: contrast {0}:1, at least 3:1 is better."] = {'de': 'Schwer lesbar auf dem Hintergrund dieses Themes: Kontrast {0}:1, mindestens 3:1 ist besser.', 'fr': 'Difficile à lire sur le fond de ce thème : contraste {0}:1, au moins 3:1 est préférable.', 'es': 'Difícil de leer sobre el fondo de este tema: contraste {0}:1, al menos 3:1 es mejor.', 'it': 'Difficile da leggere sullo sfondo di questo tema: contrasto {0}:1, almeno 3:1 è meglio.', 'pt': 'Difícil de ler sobre o fundo deste tema: contraste {0}:1, pelo menos 3:1 é melhor.', 'nl': 'Moeilijk leesbaar op de achtergrond van dit thema: contrast {0}:1, minstens 3:1 is beter.', 'pl': 'Trudny do odczytania na tle tego motywu: kontrast {0}:1, lepiej co najmniej 3:1.'}
EXTRA['The quick brown fox jumps over the lazy dog'] = {'de': 'Zwölf Boxkämpfer jagen Viktor quer über den großen Sylter Deich', 'fr': 'Portez ce vieux whisky au juge blond qui fume', 'es': 'El veloz murciélago hindú comía feliz cardillo y kiwi', 'it': 'Quel vituperabile xenofobo zelante assaggia il whisky ed esclama alleluja', 'pt': 'À noite, vovô Kowalsky vê o ímã cair no pé do pinguim', 'nl': "Pa's wijze lynx bezag vroom het fikse aquaduct", 'pl': 'Pchnąć w tę łódź jeża lub ośm skrzyń fig'}
EXTRA['a comment, dimmer'] = {'de': 'ein Kommentar, gedämpfter', 'fr': 'un commentaire, plus discret', 'es': 'un comentario, más tenue', 'it': 'un commento, più tenue', 'pt': 'um comentário, mais esbatido', 'nl': 'een opmerking, gedempter', 'pl': 'komentarz, przygaszony'}
EXTRA['3D flip to wallpaper on hover'] = {
    'de': '3D-Drehung zum Hintergrund beim Überfahren',
    'fr': "Rotation 3D vers le fond d'écran au survol",
    'es': 'Giro 3D al fondo al pasar el ratón',
    'it': 'Rotazione 3D verso lo sfondo al passaggio',
    'pt': 'Rotação 3D para o papel de parede ao passar o rato',
    'nl': '3D-draai naar achtergrond bij aanwijzen',
    'pl': 'Obrót 3D do tapety po najechaniu',
}
EXTRA["Hovering a theme card flips its preview in 3D to show the theme's wallpaper."] = {
    'de': 'Beim Überfahren einer Theme-Karte dreht sich ihre Vorschau in 3D und zeigt den Hintergrund des Themes.',
    'fr': "Au survol d'une carte de thème, son aperçu pivote en 3D pour montrer le fond d'écran du thème.",
    'es': 'Al pasar el ratón sobre la tarjeta de un tema, su vista previa gira en 3D para mostrar el fondo del tema.',
    'it': 'Passando il puntatore sulla scheda di un tema, la sua anteprima ruota in 3D per mostrare lo sfondo del tema.',
    'pt': 'Ao passar o rato sobre o cartão de um tema, a pré-visualização roda em 3D para mostrar o papel de parede do tema.',
    'nl': 'Wanneer je een themakaart aanwijst, draait het voorbeeld in 3D om de achtergrond van het thema te tonen.',
    'pl': 'Po najechaniu na kartę motywu jej podgląd obraca się w 3D, pokazując tapetę motywu.',
}
EXTRA['Current background'] = {
    'de': 'Aktueller Hintergrund',
    'fr': "Fond d'écran actuel",
    'es': 'Fondo actual',
    'it': 'Sfondo attuale',
    'pt': 'Papel de parede atual',
    'nl': 'Huidige achtergrond',
    'pl': 'Bieżące tło',
}
EXTRA['Apply theme with this wallpaper'] = {
    'de': 'Theme mit diesem Hintergrund anwenden',
    'fr': "Appliquer le thème avec ce fond d'écran",
    'es': 'Aplicar tema con este fondo',
    'it': 'Applica tema con questo sfondo',
    'pt': 'Aplicar tema com este papel de parede',
    'nl': 'Thema met deze achtergrond toepassen',
    'pl': 'Zastosuj motyw z tą tapetą',
}
EXTRA['3D rotation delay'] = {
    'de': 'Verzögerung der 3D-Drehung',
    'fr': 'Délai de rotation 3D',
    'es': 'Retardo del giro 3D',
    'it': 'Ritardo rotazione 3D',
    'pt': 'Atraso da rotação 3D',
    'nl': 'Vertraging voor 3D-draai',
    'pl': 'Opóźnienie obrotu 3D',
}
EXTRA['Seconds before a selected theme card starts rotating. 0 keeps the preview and shows left/right arrows immediately.'] = {
    'de': 'Sekunden, bis sich eine ausgewählte Theme-Karte zu drehen beginnt. 0 behält die Vorschau und zeigt sofort Links-/Rechtspfeile.',
    'fr': 'Secondes avant que la carte du thème sélectionné commence à pivoter. 0 garde l’aperçu et affiche aussitôt les flèches gauche/droite.',
    'es': 'Segundos antes de que la tarjeta del tema seleccionado empiece a girar. 0 mantiene la vista previa y muestra enseguida las flechas izquierda/derecha.',
    'it': 'Secondi prima che la scheda del tema selezionato inizi a ruotare. 0 mantiene l’anteprima e mostra subito le frecce sinistra/destra.',
    'pt': 'Segundos até o cartão do tema selecionado começar a rodar. 0 mantém a pré-visualização e mostra logo as setas esquerda/direita.',
    'nl': 'Seconden voordat een geselecteerde themakaart begint te draaien. 0 behoudt het voorbeeld en toont direct pijl-links/rechts.',
    'pl': 'Sekundy do rozpoczęcia obrotu wybranej karty motywu. 0 zachowuje podgląd i od razu pokazuje strzałki w lewo/prawo.',
}
EXTRA['0 s (arrows only)'] = {
    'de': '0 s (nur Pfeile)',
    'fr': '0 s (flèches seules)',
    'es': '0 s (solo flechas)',
    'it': '0 s (solo frecce)',
    'pt': '0 s (só setas)',
    'nl': '0 s (alleen pijlen)',
    'pl': '0 s (tylko strzałki)',
}
EXTRA['{0} s'] = {
    'de': '{0} s',
    'fr': '{0} s',
    'es': '{0} s',
    'it': '{0} s',
    'pt': '{0} s',
    'nl': '{0} s',
    'pl': '{0} s',
}
EXTRA['Apply theme when pinning wallpaper'] = {
    'de': 'Theme beim Anpinnen eines Hintergrunds anwenden',
    'fr': 'Appliquer le thème lors de l’épinglage d’un fond d’écran',
    'es': 'Aplicar el tema al fijar un fondo',
    'it': 'Applica il tema fissando uno sfondo',
    'pt': 'Aplicar o tema ao fixar um papel de parede',
    'nl': 'Thema toepassen bij vastzetten van achtergrond',
    'pl': 'Zastosuj motyw przy przypinaniu tapety',
}
EXTRA['On: pinning a wallpaper on another theme switches to that theme immediately. Off: only saves it as that theme’s current wallpaper.'] = {
    'de': 'Ein: Das Anpinnen eines Hintergrunds bei einem anderen Theme wechselt sofort zu diesem Theme. Aus: Speichert ihn nur als aktuellen Hintergrund dieses Themes.',
    'fr': 'Activé : épingler un fond d’écran sur un autre thème bascule aussitôt vers ce thème. Désactivé : l’enregistre seulement comme fond actuel de ce thème.',
    'es': 'Activado: fijar un fondo en otro tema cambia enseguida a ese tema. Desactivado: solo lo guarda como fondo actual de ese tema.',
    'it': 'Attivo: fissare uno sfondo su un altro tema passa subito a quel tema. Disattivo: lo salva solo come sfondo attuale di quel tema.',
    'pt': 'Ligado: fixar um papel de parede noutro tema muda logo para esse tema. Desligado: guarda-o apenas como papel de parede atual desse tema.',
    'nl': 'Aan: een achtergrond vastzetten op een ander thema schakelt direct naar dat thema. Uit: slaat die alleen op als huidige achtergrond van dat thema.',
    'pl': 'Włączone: przypięcie tapety w innym motywie od razu przełącza na ten motyw. Wyłączone: zapisuje ją tylko jako bieżącą tapetę tego motywu.',
}
EXTRA['Current wallpaper for this theme'] = {
    'de': 'Aktueller Hintergrund dieses Themes',
    'fr': 'Fond d’écran actuel de ce thème',
    'es': 'Fondo actual de este tema',
    'it': 'Sfondo attuale di questo tema',
    'pt': 'Papel de parede atual deste tema',
    'nl': 'Huidige achtergrond van dit thema',
    'pl': 'Bieżąca tapeta tego motywu',
}
EXTRA['Set as wallpaper for this theme'] = {
    'de': 'Als Hintergrund dieses Themes festlegen',
    'fr': 'Définir comme fond d’écran de ce thème',
    'es': 'Establecer como fondo de este tema',
    'it': 'Imposta come sfondo di questo tema',
    'pt': 'Definir como papel de parede deste tema',
    'nl': 'Instellen als achtergrond van dit thema',
    'pl': 'Ustaw jako tapetę tego motywu',
}
