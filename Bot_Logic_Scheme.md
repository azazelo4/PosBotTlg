---
config:
  layout: fixed
  look: neo
---
flowchart TD
 subgraph s1["Auth.py"]
        D(("Авторизация пользователя"))
  end
 subgraph s2["Database.py"]
        Z["подключение к базе данных и работа с данными"]
  end
 subgraph s3["sale.py"]
        M["Поиск товара в БД"]
        N["Ввод суммы продажи"]
        O["Проверка минимальной цены"]
        P["Выбор типа оплаты"]
        Q["Подтверждение продажи"]
        R["Запись продажи в таблицу продаж и вычет из количетсва остатков товара"]
        K["Запуск процесса продажи"]
        L["Ввод артикула"]
        n22["Вывод информации о товаре"]
  end
 subgraph s4["return.py"]
        n9["Поиск в БД продаж <br>"]
        n10["Вывод информации о продаже"]
        n7["ввод суммы возврата"]
        n12["проверка корректности возврата"]
        n11["потверждение возврата"]
        n13["удаление записи из таблицы продаж и добавление количества в таблицу с товаром"]
        S["Запуск процесса Возврата <br>"]
        n16["Ввод артикула <br>"]
  end
 subgraph s5["report.py"]
        n15["Запуск отчетов <br>"]
        n17["ввод периода отчетов"]
        n18["генерация отчета из выбранного периода"]
        n19["Конвертация в XLS формат"]
        n20["отправка пользователю отчета"]
        n21["Выборка продаж за выбранный период из таблицы продаж <br>"]
  end
    A["Запуск бота start"] --> B["Отправка запроса на предоставление контакта"]
    B --> C(["Пользователь делится контактом"])
    C --> D
    E["Роль пользователя?"] -- cashier --> F["Отправка кнопок для кассира"]
    E -- admin --> G["Отправка кнопок для администратора"]
    F --> H(["Команда Касса"])
    G --> H & n1(["Команда Персонал <br>"])
    H --> I["выдача клавиатуры кассовых операций"]
    I --> J{"Выбор операции"}
    J -- Продажа --> K
    K --> L
    L --> M
    M --> n22
    N --> O
    O --> P
    P --> Q
    Q --> R
    J -- Возврат --> S
    E -- иные роли --> T(["нет доступа"])
    n1 --> n2["Обработка Операций с персоналом"]
    n2 --> n5["Выбор операции"]
    R --> n6["Возвращение в главное меню <br>"]
    n6 --> E & E
    n5 -- добавить --> n8["запуск процесса добавления пользователя"]
    n5 -- удалить --> n14["Процесс Удаления (деактивации) пользователя"]
    n9 --> n10
    n10 --> n7
    n7 --> n12
    n12 --> n11
    n11 --> n13
    n13 --> n6
    J -- Отчеты --> n15
    S --> n16
    n16 --> s4 & n9
    n15 --> n17
    n17 --> n21
    n18 --> n19
    n19 --> n20
    n20 --> n6
    n21 --> n18
    n22 --> N
    s3 <--> s2
    s4 <--> s2
    s1 <--> s2
    n5 -- права --> n23["запуск процесса редактирования пользователя"]
    T --> B
    J -- отмена --> n24["Отмена операций <br>"]
    n24 --> n6 & s4 & s3
    J -- назад --> n6
    s2 <--> s5
    D --> E
    n25["ведётся работа"] --> n27["Дебаг"]
    n27 --> n28["тест <br>"]
    n28 --> n26["В рабочем состоянии <br>"]
    n5 -- назад --> n6
    Z@{ shape: cyl}
    M@{ shape: h-cyl}
    N@{ shape: dbl-circ}
    O@{ shape: h-cyl}
    P@{ shape: dbl-circ}
    Q@{ shape: dbl-circ}
    R@{ shape: h-cyl}
    K@{ shape: h-cyl}
    L@{ shape: dbl-circ}
    n22@{ shape: hex}
    n9@{ shape: h-cyl}
    n10@{ shape: hex}
    n7@{ shape: dbl-circ}
    n12@{ shape: hex}
    n11@{ shape: dbl-circ}
    n13@{ shape: h-cyl}
    S@{ shape: h-cyl}
    n16@{ shape: dbl-circ}
    n15@{ shape: h-cyl}
    n17@{ shape: diam}
    n18@{ shape: h-cyl}
    n19@{ shape: h-cyl}
    n20@{ shape: hex}
    n21@{ shape: h-cyl}
    A@{ shape: extract}
    B@{ shape: hex}
    E@{ shape: manual-input}
    F@{ shape: hex}
    G@{ shape: hex}
    I@{ shape: h-cyl}
    n2@{ shape: h-cyl}
    n5@{ shape: diam}
    n8@{ shape: h-cyl}
    n14@{ shape: h-cyl}
    n23@{ shape: h-cyl}
    n25@{ shape: rect}
    n27@{ shape: rect}
    n28@{ shape: rect}
    n26@{ shape: rect}
    style n15 fill:#D50000
    style n17 fill:#00C853
    style n18 fill:#616161
    style n19 fill:#616161
    style n20 fill:#616161
    style n21 fill:#616161
    style A fill:#00C853,stroke-width:4px,stroke-dasharray: 5
    style B fill:#00C853
    style C fill:#00C853
    style E fill:#00C853
    style F fill:#00C853
    style G fill:#00C853
    style H fill:#00C853
    style n1 fill:#00C853
    style I fill:#00C853
    style J fill:#FFD600
    style T fill:#00C853
    style n2 fill:#FFD600
    style n5 fill:#FFD600
    style n6 fill:#FFF9C4
    style n8 fill:#FFD600
    style n14 fill:#FFD600
    style s4 fill:#D50000
    style s3 fill:#00C853
    style s2 fill:#FFF9C4
    style s1 fill:#00C853
    style n23 fill:#FFD600
    style n24 fill:#00C853
    style s5 fill:#FF6D00
    style n25 fill:#D50000
    style n27 fill:#FFD600
    style n28 fill:#FFF9C4
    style n26 fill:#00C853

test
