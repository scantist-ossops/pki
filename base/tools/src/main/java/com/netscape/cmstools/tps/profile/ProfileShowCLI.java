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
// (C) 2013 Red Hat, Inc.
// All rights reserved.
// --- END COPYRIGHT BLOCK ---

package com.netscape.cmstools.tps.profile;

import java.io.FileWriter;
import java.io.PrintWriter;

import org.apache.commons.cli.CommandLine;
import org.apache.commons.cli.Option;
import org.dogtagpki.cli.CommandCLI;

import com.netscape.certsrv.tps.profile.ProfileClient;
import com.netscape.certsrv.tps.profile.ProfileData;
import com.netscape.cmstools.cli.MainCLI;

/**
 * @author Endi S. Dewata
 */
public class ProfileShowCLI extends CommandCLI {

    public static org.slf4j.Logger logger = org.slf4j.LoggerFactory.getLogger(ProfileShowCLI.class);

    public ProfileCLI profileCLI;

    public ProfileShowCLI(ProfileCLI profileCLI) {
        super("show", "Show profile", profileCLI);
        this.profileCLI = profileCLI;
    }

    @Override
    public void printHelp() {
        formatter.printHelp(getFullName() + " <Profile ID> [OPTIONS...]", options);
    }

    @Override
    public void createOptions() {
        Option option = new Option(null, "output", true, "Output file to store profile properties.");
        option.setArgName("file");
        options.addOption(option);
    }

    @Override
    public void execute(CommandLine cmd) throws Exception {

        String[] cmdArgs = cmd.getArgs();

        if (cmdArgs.length != 1) {
            throw new Exception("No Profile ID specified.");
        }

        String profileID = cmdArgs[0];
        String output = cmd.getOptionValue("output");

        MainCLI mainCLI = (MainCLI) getRoot();
        mainCLI.init();

        ProfileClient profileClient = profileCLI.getProfileClient();
        ProfileData profileData = profileClient.getProfile(profileID);

        if (output == null) {
            MainCLI.printMessage("Profile \"" + profileID + "\"");
            ProfileCLI.printProfileData(profileData, true);

        } else {
            try (PrintWriter out = new PrintWriter(new FileWriter(output))) {
                out.println(profileData);
            }
            MainCLI.printMessage("Stored profile \"" + profileID + "\" into " + output);
        }
    }
}
