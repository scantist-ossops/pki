// --- BEGIN COPYRIGHT BLOCK ---
// This program is free software; you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation; version 2 of the License.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License along
// with this program; if not, write to the Free Software Foundation, Inc.,
// 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
//
// (C) 2019 Red Hat, Inc.
// All rights reserved.
// --- END COPYRIGHT BLOCK ---

package org.dogtagpki.server.cli;

import org.dogtagpki.cli.CLI;

/**
 * @author Endi S. Dewata
 */
public class SubsystemDBCLI extends CLI {

    public SubsystemDBCLI(CLI parent) {
        super("db", parent.name.toUpperCase() + " database management commands", parent);

        addModule(new SubsystemDBInfoCLI(this));
        addModule(new SubsystemDBInitCLI(this));
        addModule(new SubsystemDBEmptyCLI(this));
        addModule(new SubsystemDBRemoveCLI(this));
        addModule(new SubsystemDBUpgradeCLI(this));

        addModule(new SubsystemDBAccessCLI(this));
        addModule(new SubsystemDBIndexCLI(this));
        addModule(new SubsystemDBReplicationCLI(this));
        addModule(new SubsystemDBVLVCLI(this));
    }
}
