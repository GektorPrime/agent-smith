package fixtures

import (
    "fmt"
    "strings"
)

const DefaultPrefix = "INV"

type Invoice struct {
    ID     int
    Number string
}

func NewInvoice(id int) *Invoice {
    return &Invoice{ID: id, Number: fmt.Sprintf("%s-%d", DefaultPrefix, id)}
}

func (i *Invoice) Slug() string {
    return strings.ToLower(i.Number)
}
