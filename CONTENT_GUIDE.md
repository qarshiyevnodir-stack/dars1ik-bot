# PDF darsliklarni qo‘shish tartibi

Bu botda fanlar va darsliklar bosqichma-bosqich qo‘shiladi. Sizda qaysi sinf va qaysi fan darsligi tayyor bo‘lsa, faqat o‘sha qismini kiritib borasiz.

## 1-qadam. PDF faylni joylash

PDF faylni `textbooks/` papkasiga joylaysiz. Fayl nomini sodda va tushunarli formatda yozish tavsiya qilinadi.

| To‘g‘ri misol | Noto‘g‘ri misol |
|---|---|
| `5-sinf-matematika.pdf` | `New Document (7).pdf` |
| `8-sinf-biologiya.pdf` | `final_version_last.pdf` |

## 2-qadam. `content.json` ichiga fan qo‘shish

Kerakli sinfni topasiz va uning `subjects` qismiga yangi fan yozasiz.

## 3-qadam. Fan ichiga darslik qo‘shish

Har bir fan ichida `textbooks` ro‘yxati bo‘ladi. Shu yerga darslik nomi va PDF fayl nomi yoziladi.

## Namuna

```json
{
  "id": "5",
  "name": "5-sinf",
  "subjects": [
    {
      "id": "matematika",
      "name": "Matematika",
      "textbooks": [
        {
          "id": "matematika-1",
          "name": "5-sinf Matematika darsligi",
          "file_name": "5-sinf-matematika.pdf"
        }
      ]
    }
  ]
}
```

## Muhim qoida

Bot fanlarni oldindan qotirib qo‘ymaydi. Siz qaysi sinfga qaysi fan darsligini topsangiz, o‘shani qo‘shib borasiz. Shu sababli loyiha juda moslashuvchan bo‘ladi.
