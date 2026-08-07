<?php

declare(strict_types=1);

use DateTimeImmutable;

const DEFAULT_STATUS = 'active';

class InvoiceService
{
    public function __construct(private string $prefix)
    {
    }

    public function buildNumber(int $id): string
    {
        return $this->prefix . '-' . $id;
    }

    public function createdAt(): DateTimeImmutable
    {
        return new DateTimeImmutable('now');
    }
}
